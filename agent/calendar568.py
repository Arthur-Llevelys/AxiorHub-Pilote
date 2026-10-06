"""Agendas multiples : autorisation explicite, identité stable, relecture après dépôt.

OAuth Google se limite aux événements et à la liste des calendriers. Aucun invité
ni notification de tiers n'est ajouté par ce connecteur.
"""
import base64
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import quote, urlencode, urlsplit
from xml.sax.saxutils import escape

from .common import Stop, HTTP, read_secret, digest, fold, xml_bytes
from . import vault567, settings568

SCOPES=['https://www.googleapis.com/auth/calendar.events','https://www.googleapis.com/auth/calendar.calendarlist.readonly']
GOOGLE_API='https://www.googleapis.com/calendar/v3'


def _secret(desk,owner,name,value):
    path=Path(desk.c['state_dir'])/'vault'/'calendars568'/digest(owner)/(name+'-'+secrets.token_hex(12)+'.secret')
    return vault567.write(path,value)


def targets(desk,owner,private=False):
    out=[]
    for raw in desk.db.execute('SELECT * FROM calendar_targets568 WHERE owner=? ORDER BY label,id',(owner,)):
        r=dict(raw);cfg=json.loads(r.pop('config'));r['enabled']=bool(r['enabled'])
        r['config']=cfg if private else {k:v for k,v in cfg.items() if not k.endswith('_file')}
        if not private:r['secret_configured']=any(v and Path(str(v)).is_file() for k,v in cfg.items() if k.endswith('_file'))
        out.append(r)
    return out


def nextcloud_url(desk,url):
    """Valide une cible sans lire le secret lors de l'affichage des paramètres."""
    from .dav import DAV
    cfg=desk.c.get('nextcloud',{})
    if not cfg.get('url') or not cfg.get('username'):raise Stop('nextcloud_non_configure')
    obj=DAV.__new__(DAV);obj.http=HTTP(cfg['url']);obj.calendar_home=obj.http.base+'/remote.php/dav/calendars/'+quote(cfg['username'],safe='')+'/'
    return obj.calendar_url(url)


def save_target(desk,data,owner):
    if set(data)-{'id','label','provider','url','base_url','username','password','calendar_id','enabled','external_approved','include_details','delete'}:raise Stop('agenda_champ_inconnu')
    ident=str(data.get('id') or secrets.token_hex(16));old=desk.db.execute('SELECT * FROM calendar_targets568 WHERE id=? AND owner=?',(ident,owner)).fetchone()
    if data.get('id') and not old:raise Stop('agenda_cible_absent')
    if data.get('delete') is True:
        if old['provider']=='nextcloud':desk.db.execute('UPDATE calendar_targets568 SET enabled=0,updated=? WHERE id=? AND owner=?',(desk.now(),ident,owner))
        else:desk.db.execute('DELETE FROM calendar_targets568 WHERE id=? AND owner=?',(ident,owner))
        desk.db.commit();return {'deleted':True}
    cfg=json.loads(old['config']) if old else {};provider=data.get('provider') or (old['provider'] if old else '')
    if provider not in ('nextcloud','caldav','google') or (old and provider!=old['provider']):raise Stop('type_agenda_invalide')
    label=str(data.get('label') or (old['label'] if old else '')).strip()
    if not 2<=len(label)<=120:raise Stop('nom_agenda_invalide')
    for key in ('enabled','external_approved','include_details'):
        if key in data and type(data[key])!=bool:raise Stop('agenda_valeur_invalide')
    enabled=data.get('enabled',bool(old['enabled']) if old else True)
    if provider=='google':
        cfg['calendar_id']=str(data.get('calendar_id') or cfg.get('calendar_id') or 'primary')
        if not re.fullmatch(r'[A-Za-z0-9_@.#+-]{1,250}',cfg['calendar_id']):raise Stop('identifiant_agenda_google_invalide')
        cfg['external_approved']=data.get('external_approved',cfg.get('external_approved',False));cfg['include_details']=data.get('include_details',cfg.get('include_details',False))
        if enabled and cfg['external_approved'] is not True:raise Stop('autorisation_agenda_google_requise')
    else:
        url=str(data.get('url') or cfg.get('url') or '').rstrip('/')+'/'
        parts=urlsplit(url)
        if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or '..' in parts.path.split('/'):raise Stop('url_agenda_invalide')
        cfg['url']=url
        if provider=='nextcloud':
            if url not in {nextcloud_url(desk,u) for u in desk.c.get('calendar',{}).get('urls',[])}:raise Stop('agenda_nextcloud_non_autorise')
        else:
            base=str(data.get('base_url') or cfg.get('base_url') or (parts.scheme+'://'+parts.netloc)).rstrip('/')
            p=urlsplit(base)
            if (p.scheme,p.netloc)!=(parts.scheme,parts.netloc) or p.path not in ('','/') or p.query or p.fragment:raise Stop('origine_agenda_invalide')
            cfg['base_url']=base;cfg['username']=str(data.get('username') or cfg.get('username') or '')
            if not 1<=len(cfg['username'])<=200 or any(ord(c)<32 for c in cfg['username']):raise Stop('identifiant_agenda_invalide')
            if data.get('password'):cfg['password_file']=_secret(desk,owner,'caldav',data['password'])
            if not cfg.get('password_file'):raise Stop('secret_agenda_requis')
    desk.db.execute('INSERT OR REPLACE INTO calendar_targets568 VALUES(?,?,?,?,?,?,?)',(ident,owner,label,provider,json.dumps(cfg),int(enabled),desk.now()));desk.db.commit()
    desk.audit('agenda568_enregistre',{'owner':owner,'id':ident,'provider':provider,'enabled':enabled})
    return next(x for x in targets(desk,owner) if x['id']==ident)


def effective_targets(desk,owner):
    rows=[x for x in targets(desk,owner,True) if x['enabled']]
    explicit={x['config'].get('url') for x in rows}
    disabled={x['config'].get('url') for x in targets(desk,owner,True) if not x['enabled']}
    if not desk.c.get('nextcloud',{}).get('url') or not desk.c.get('nextcloud',{}).get('username'):return rows
    for url in desk.c.get('calendar',{}).get('urls',[]):
        normalized=nextcloud_url(desk,url)
        if normalized not in explicit and normalized not in disabled:rows.append({'id':'nextcloud-'+digest(normalized)[:24],'owner':owner,'label':'Nextcloud · '+normalized.rstrip('/').rsplit('/',1)[-1],
          'provider':'nextcloud','config':{'url':normalized},'enabled':True})
    return rows


def oauth_start(desk,owner):
    c=desk.c.get('google_calendar568',{});cid=str(c.get('client_id') or '');redirect=str(c.get('redirect_uri') or '')
    if not cid or not c.get('client_secret_file'):raise Stop('google_oauth_non_configure')
    p=urlsplit(redirect)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment or not p.path.endswith('/api440/m568/google/callback'):raise Stop('google_redirect_invalide')
    read_secret(c['client_secret_file']);state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    desk.db.execute('DELETE FROM google_oauth568 WHERE expires<?',(time.time(),))
    desk.db.execute('INSERT INTO google_oauth568 VALUES(?,?,?,?,?,?,?)',(digest(state),owner,redirect,verifier,digest(cid+'|'+str(c['client_secret_file'])),time.time()+600,0));desk.db.commit()
    return {'url':'https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({'client_id':cid,'redirect_uri':redirect,
      'response_type':'code','scope':' '.join(SCOPES),'access_type':'offline','prompt':'consent','state':state,
      'code_challenge':challenge,'code_challenge_method':'S256'}),'redirect_uri':redirect,'expires_seconds':600}


def _token_request(data):
    h=HTTP('https://oauth2.googleapis.com',timeout=30)
    raw=h.request('POST',h.base+'/token',urlencode(data).encode(),{'Content-Type':'application/x-www-form-urlencoded'},30000)
    try:result=json.loads(raw)
    except (ValueError,UnicodeError):raise Stop('google_oauth_reponse_invalide') from None
    if not isinstance(result,dict) or not result.get('access_token') or result.get('token_type','').lower()!='bearer':raise Stop('google_oauth_refuse')
    return result


def oauth_finish(desk,owner,args):
    token=str(args.get('state') or '');code=str(args.get('code') or '')
    if len(token)>200 or not 1<=len(code)<=3000:raise Stop('google_oauth_code_absent')
    desk.db.execute('BEGIN IMMEDIATE')
    r=desk.db.execute('SELECT * FROM google_oauth568 WHERE state_hash=? AND owner=? AND used=0 AND expires>?',(digest(token),owner,time.time())).fetchone()
    if not r:desk.db.rollback();raise Stop('google_oauth_etat_invalide')
    c=desk.c.get('google_calendar568',{})
    if r['config_hash']!=digest(str(c.get('client_id',''))+'|'+str(c.get('client_secret_file',''))):desk.db.rollback();raise Stop('google_oauth_configuration_modifiee')
    desk.db.execute('UPDATE google_oauth568 SET used=1 WHERE state_hash=?',(digest(token),));desk.db.commit()
    out=_token_request({'client_id':c['client_id'],'client_secret':read_secret(c['client_secret_file']),
        'redirect_uri':r['redirect'],'code':code,'code_verifier':r['verifier'],'grant_type':'authorization_code'})
    if set(SCOPES)-set(str(out.get('scope','')).split()):raise Stop('google_oauth_permissions_incompletes')
    if not out.get('refresh_token'):raise Stop('google_oauth_jeton_renouvellement_absent')
    path=_secret(desk,owner,'google-refresh',out['refresh_token'])
    desk.setting('calendar568:google:'+owner,{'refresh_token_file':path,'client_id':c['client_id'],'connected':desk.now()})
    desk.audit('google_calendar568_connecte',{'owner':owner})
    return {'connected':True,'message':'Google connecté. Sélectionnez les agendas à alimenter.'}


def disconnect(desk,owner):
    value=desk.settings('calendar568:google:'+owner,{})
    # Révocation auprès de Google demandée explicitement ; jeton jamais placé dans une URL.
    if value.get('refresh_token_file'):
        token=read_secret(value['refresh_token_file']);h=HTTP('https://oauth2.googleapis.com',timeout=30)
        h.request('POST',h.base+'/revoke',urlencode({'token':token}).encode(),{'Content-Type':'application/x-www-form-urlencoded'},30000)
        Path(value['refresh_token_file']).unlink(missing_ok=True)
    desk.setting('calendar568:google:'+owner,{})
    desk.db.execute("UPDATE calendar_targets568 SET enabled=0 WHERE owner=? AND provider='google'",(owner,));desk.db.commit()
    return {'disconnected':True}


class Google:
    def __init__(self,desk,owner):
        self.desk=desk;self.owner=owner;c=desk.c.get('google_calendar568',{});auth=desk.settings('calendar568:google:'+owner,{})
        if not auth.get('refresh_token_file') or auth.get('client_id')!=c.get('client_id'):raise Stop('google_agenda_non_connecte')
        out=_token_request({'grant_type':'refresh_token','refresh_token':read_secret(auth['refresh_token_file']),
           'client_id':c['client_id'],'client_secret':read_secret(c['client_secret_file'])})
        self.http=HTTP(GOOGLE_API,timeout=30);self.http.headers['Authorization']='Bearer '+out['access_token']
    def calendars(self):
        out=[];page=''
        for _ in range(10):
            r=self.http.json('GET','/users/me/calendarList?'+urlencode({'maxResults':250,**({'pageToken':page} if page else {})}))
            for x in r.get('items',[]):out.append({'id':x.get('id'),'label':x.get('summary'),'access':x.get('accessRole'),'primary':x.get('primary',False)})
            page=r.get('nextPageToken','')
            if not page:return out
        raise Stop('google_liste_agendas_incomplete')
    def events(self,calendar,start,end):
        out=[];page=''
        for _ in range(10):
            r=self.http.json('GET','/calendars/'+quote(calendar,safe='')+'/events?'+urlencode({'timeMin':start.isoformat(),'timeMax':end.isoformat(),'singleEvents':'true','showDeleted':'false','maxResults':250,**({'pageToken':page} if page else {})}))
            out.extend(r.get('items',[]));page=r.get('nextPageToken','')
            if not page:return out
        raise Stop('google_evenements_lecture_incomplete')
    def get(self,calendar,ident):
        return self.http.json('GET','/calendars/'+quote(calendar,safe='')+'/events/'+quote(ident,safe=''))
    def put(self,calendar,ident,payload):
        return self.http.json('POST','/calendars/'+quote(calendar,safe='')+'/events?sendUpdates=none',{'id':ident,**payload})


def _http(desk,target):
    cfg=target['config']
    if target['provider']=='nextcloud':
        from .dav import DAV
        return DAV(desk.c['nextcloud']).http
    return HTTP(cfg['base_url'],cfg['username'],read_secret(cfg['password_file']),timeout=30)


def _dav_events(http,url,start,end,tz):
    from .dav import D,C,parse_events
    fmt=lambda d:d.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    body=('<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:getetag/><c:calendar-data><c:expand start="'+fmt(start)+'" end="'+fmt(end)+'"/></c:calendar-data></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:time-range start="'+fmt(start)+'" end="'+fmt(end)+'"/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>')
    root=xml_bytes(http.request('REPORT',url,body.encode(),{'Content-Type':'application/xml; charset=utf-8','Depth':'1'}))
    out=[]
    for r in root.findall(D+'response'):
        ok=False
        for ps in r.findall(D+'propstat'):
            if ' 200 ' not in ps.findtext(D+'status',''):continue
            data=ps.findtext('.//'+C+'calendar-data')
            if data is None:continue
            for event in parse_events(data,tz):event['href']=r.findtext(D+'href','');out.append(event)
            ok=True
        if not ok:raise Stop('agenda_lecture_partielle')
    return out


def _normal(event,tz='Europe/Paris'):
    from zoneinfo import ZoneInfo
    if isinstance(event.get('start'),dict):
        def val(key):return str(event[key].get('dateTime') or event[key].get('date') or '')
        start,end=val('start'),val('end');title=event.get('summary','');description=event.get('description','')
    else:
        start,end=str(event.get('start','')),str(event.get('end',''));title=event.get('summary') or event.get('title','');description=event.get('description','')
        if event.get('all_day'):start,end=start[:10],end[:10]
    def canon(x):
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',x):return x
        try:return datetime.fromisoformat(x.replace('Z','+00:00')).astimezone(timezone.utc).isoformat()
        except ValueError:return x
    return (canon(start),canon(end),normalized_title(title),str(description))


def normalized_title(text):return re.sub(r'\s+',' ',fold(str(text))).strip()


def event_payload(desk,event,matter,target):
    title=str(event['title'])[:250];description=str(event.get('description') or '')[:1900]
    if target['provider']=='google' and not target['config'].get('include_details',False):
        title='Échéance AxiorHub · '+matter+' · '+str(event.get('kind') or 'procédure');description='Rappel préparé par AxiorHub. Consulter le dossier dans l’application.'
    a,b=event['start'],event['end'];all_day=bool(event.get('all_day'))
    key='date' if all_day else 'dateTime'
    return {'summary':title,'description':description,'start':{key:a},'end':{key:b},'visibility':'private','reminders':{'useDefault':False},'extendedProperties':{'private':{'axiorhubKey':event['key'],'axiorhubMatter':matter}}}


def _ical(event,payload):
    def esc(v):return str(v).replace('\\','\\\\').replace(';','\\;').replace(',','\\,').replace('\r','').replace('\n','\\n')
    def dt(v):return datetime.fromisoformat(v.replace('Z','+00:00')).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    all_day=event.get('all_day');a=event['start'].replace('-','') if all_day else dt(event['start']);b=event['end'].replace('-','') if all_day else dt(event['end'])
    suffix=';VALUE=DATE' if all_day else ''
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//AxiorHub//Document Agents 5.6.8//FR','BEGIN:VEVENT',
      'UID:'+event['key']+'@axiorhub.local','DTSTAMP:'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
      'DTSTART'+suffix+':'+a,'DTEND'+suffix+':'+b,'SUMMARY:'+esc(payload['summary']),'DESCRIPTION:'+esc(payload['description']),
      'CLASS:PRIVATE','STATUS:TENTATIVE','END:VEVENT','END:VCALENDAR']
    # Repli RFC 5545 à 75 octets, sans couper un caractère UTF-8.
    folded=[]
    for line in lines:
        current=''
        for char in line:
            if len((current+char).encode())>75:folded.append(current);current=' '+char
            else:current+=char
        folded.append(current)
    return ('\r\n'.join(folded)+'\r\n').encode()


@contextmanager
def calendar_lock(desk,target):
    root=Path(desk.c['state_dir'])/'calendar-locks568';root.mkdir(mode=0o700,exist_ok=True)
    key=target['provider']+'|'+str(target['config'].get('url') or target['config'].get('calendar_id'))
    with (root/(digest(key)+'.lock')).open('a') as handle:
        os.chmod(handle.name,0o600)
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Stop('agenda_depot_deja_en_cours') from None
        yield


def deposit(desk,owner,matter,event,target,google=None):
    with calendar_lock(desk,target):return _deposit(desk,owner,matter,event,target,google)


def _deposit(desk,owner,matter,event,target,google=None):
    settings568.check_owner(desk,owner)
    if target['provider']=='google':
        from .hybrid400 import policy
        if target['config'].get('external_approved') is not True or matter in policy(desk.c)['excluded_matters']:raise Stop('agenda_google_dossier_ou_transmission_interdite')
    # Écriture uniquement dans une cible autorisée. Son choix n'est jamais fourni par un LLM.
    payload=event_payload(desk,event,matter,target);key=event['key'];ident='ax'+digest(owner+'|'+target['id']+'|'+key)[:56]
    row=desk.db.execute('SELECT * FROM calendar_deposits568 WHERE owner=? AND target_id=? AND event_key=?',(owner,target['id'],key)).fetchone()
    tz=settings568.profile(desk,owner)['timezone']
    from zoneinfo import ZoneInfo
    def parsed(v):return datetime.combine(date.fromisoformat(v),datetime.min.time(),ZoneInfo(tz)) if len(v)==10 else datetime.fromisoformat(v.replace('Z','+00:00'))
    start,end=parsed(event['start'])-timedelta(days=1),parsed(event['end'])+timedelta(days=1)
    g=(google or Google(desk,owner)) if target['provider']=='google' else None;http=None
    if g:
        calendar=target['config']['calendar_id'];existing=g.events(calendar,start,end)
    else:
        http=_http(desk,target);existing=_dav_events(http,target['config']['url'],start,end,tz)
    desired=_normal(payload,tz);equiv=[]
    for item in existing:
        if item.get('status')=='cancelled':continue
        n=_normal(item,tz)
        same_key=(item.get('uid')==key+'@axiorhub.local' or item.get('id')==ident or item.get('extendedProperties',{}).get('private',{}).get('axiorhubKey')==key)
        if same_key:
            if n[:3]!=desired[:3]:raise Stop('agenda_evenement_modifie_par_utilisateur')
            equiv.append(item)
        else:
            reference=bool(matter and re.search(r'(?<![\w-])'+re.escape(matter)+r'(?![\w-])',n[2]+' '+n[3],re.I))
            category=str(event.get('kind',''));same_case_kind=reference and bool(category) and category in n[2]
            # La durée d'une audience n'est souvent pas fournie dans l'avis.
            # Conserver l'événement humain existant, même si sa durée diffère.
            same_start=n[0]==desired[0] or (event.get('all_day') and n[0][:10]==desired[0][:10])
            if (n[:2]==desired[:2] and n[2]==desired[2]) or (same_start and same_case_kind):equiv.append(item)
    if len(equiv)>1:raise Stop('agenda_doublons_a_verifier')
    def saveproof(item,adopted=False):
        actual=_normal(item,tz)
        proof={'verified_at':desk.now(),'target':target['label'],'provider':target['provider'],'uid':item.get('uid') or item.get('id'),
         'start':actual[0],'end':actual[1],'adopted_existing':adopted,'event_key':key,'source_start':event['start'],'source_end':event['end']}
        desk.db.execute('INSERT OR REPLACE INTO calendar_deposits568 VALUES(?,?,?,?,?,?,?)',(owner,target['id'],key,str(proof['uid']),'verified',json.dumps(proof),desk.now()));desk.db.commit();return proof
    if equiv:return saveproof(equiv[0],True)
    if row and row['state'] in ('uncertain','verified'):raise Stop('agenda_depot_incertain_ou_supprime_verifier_avant_relance')
    desk.db.execute('INSERT OR REPLACE INTO calendar_deposits568 VALUES(?,?,?,?,?,?,?)',(owner,target['id'],key,ident,'uncertain','{}',desk.now()));desk.db.commit()
    if g:
        try:g.put(calendar,ident,payload)
        except Stop as exc:
            if str(exc)!='http_409':raise
        actual=g.get(calendar,ident)
        if _normal(actual,tz)[:3]!=desired[:3] or actual.get('attendees'):raise Stop('agenda_depot_non_conforme')
        return saveproof(actual)
    url=target['config']['url']+ident+'.ics';raw=_ical(event,payload)
    try:http.request('PUT',url,raw,{'Content-Type':'text/calendar; charset=utf-8','If-None-Match':'*'},200000)
    except Stop as exc:
        if str(exc)!='http_412':raise
    from .dav import parse_events
    read=http.request('GET',url,limit=200000).decode('utf-8');actual=parse_events(read,tz)
    if len(actual)!=1 or _normal(actual[0],tz)[:3]!=desired[:3] or re.search(r'(?mi)^ATTENDEE',read):raise Stop('agenda_depot_non_conforme')
    return saveproof(actual[0])


def diagnostic(desk,owner,ident=''):
    if not ident:return {'google_connected':bool(desk.settings('calendar568:google:'+owner,{})), 'google_calendars':Google(desk,owner).calendars()}
    target=next((x for x in targets(desk,owner,True) if x['id']==ident),None)
    if not target:raise Stop('agenda_cible_absent')
    if target['provider']=='google':
        rows=Google(desk,owner).calendars();wanted=target['config']['calendar_id']
        selected=next((x for x in rows if x['id']==wanted or (wanted=='primary' and x['primary'])),None)
        if not selected or selected['access'] not in ('owner','writer'):raise Stop('agenda_google_non_inscriptible')
    else:
        h=_http(desk,target);xml_bytes(h.request('PROPFIND',target['config']['url'],b'<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/><d:current-user-privilege-set/></d:prop></d:propfind>',{'Depth':'0','Content-Type':'application/xml'},200000))
    return {'status':'ok','message':'Lecture de la cible réussie ; aucun événement créé.'}

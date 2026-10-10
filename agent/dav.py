from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
import os
from contextlib import contextmanager
import time
import stat
import secrets
import hashlib
import errno
from pathlib import Path, PurePosixPath
import tempfile
import re
import urllib.parse as U
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from .common import HTTP, Stop, clean_path, under, xml_bytes, read_secret, fold, digest
from .documents import extract, SUPPORTED

D = '{DAV:}'
C = '{urn:ietf:params:xml:ns:caldav}'
OC = '{http://owncloud.org/ns}'


class DAV:
    def __new__(cls, cfg=None, *args, **kwargs):
        # 5.6.22 : un dossier de travail local (client Nextcloud de bureau, partage monté) prend la place de WebDAV pour les
        # fichiers, avec la même interface ; les agendas restent en CalDAV si l'accès WebDAV est aussi renseigné.
        if cls is DAV and isinstance(cfg, dict) and cfg.get('local_path'):
            return object.__new__(LocalFolder)
        return object.__new__(cls)

    def __init__(self, cfg):
        self.cfg = cfg
        self.http = HTTP(cfg['url'], cfg['username'], read_secret(cfg['password_file']))
        self.files = self.http.base + '/remote.php/dav/files/' + U.quote(cfg['username'], safe='')
        self.calendar_home = self.http.base + '/remote.php/dav/calendars/' + U.quote(cfg['username'], safe='') + '/'

    def request_xml(self, method, url, body, depth='1'):
        return xml_bytes(self.http.request(method, url, body.encode(),
                         {'Content-Type': 'application/xml; charset=utf-8', 'Depth': depth}))

    def file_url(self, path):
        path = clean_path(path)
        if not any(under(path, root) for root in self.cfg['roots']): raise Stop('fichier_hors_racines')
        if any(p.startswith('.') or fold(p) in {'secrets', 'mots de passe'} for p in path.split('/') if p):
            raise Stop('repertoire_exclu')
        return self.files + U.quote(path, safe='/')

    def href_path(self, href):
        absolute = U.urljoin(self.http.base + '/', href)
        p, origin = U.urlsplit(absolute), U.urlsplit(self.files)
        if (p.scheme, p.netloc) != (origin.scheme, origin.netloc) or p.query or p.fragment:
            raise Stop('href_dav_externe')
        path, prefix = U.unquote(p.path), U.unquote(origin.path)
        if not path.startswith(prefix + '/') and path != prefix: raise Stop('href_dav_hors_compte')
        relative = clean_path(path[len(prefix):])
        if not any(under(relative,root) for root in self.cfg['roots']): raise Stop('fichier_hors_racines')
        return relative

    def list_folder(self, path):
        body = '<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getetag/><d:creationdate/><d:getlastmodified/><d:getcontentlength/></d:prop></d:propfind>'
        root = self.request_xml('PROPFIND', self.file_url(path) + '/', body)
        out = []
        for response in root.findall(D+'response'):
            href = response.findtext(D+'href', '')
            prop = None
            for ps in response.findall(D+'propstat'):
                if ' 200 ' in ps.findtext(D+'status', ''): prop = ps.find(D+'prop'); break
            if prop is None: raise Stop('entree_dav_inaccessible')
            try:
                p = self.href_path(href)
            except Stop as ex:
                # 5.6.3 : un nom de fichier ambigu (« %41 », antislash, caractère de contrôle) est ignoré et signalé, jamais accepté ;
                # il ne bloque plus la lecture du reste du dossier.
                if str(ex) != 'chemin_refuse':
                    raise
                self.refused = getattr(self, 'refused', []) + [href[-120:]]
                continue
            if p == clean_path(path): continue
            if not under(p, path): raise Stop('reponse_dav_hors_dossier')
            if any(s.startswith('.') or fold(s) in {'secrets','mots de passe'} for s in p.split('/') if s): continue
            out.append({'path': p, 'directory': prop.find('.//'+D+'collection') is not None,
                        'etag': prop.findtext(D+'getetag', ''),
                        'modified': prop.findtext(D+'getlastmodified', ''),
                        'created': prop.findtext(D+'creationdate', ''),
                        'size': int(prop.findtext(D+'getcontentlength', '0') or 0)})
        return out

    def file_web_url(self, path):
        """Return the authenticated Nextcloud file route used by OnlyOffice.

        The file remains in Nextcloud; this method does not create a public
        share and does not contact the document server directly.
        """
        body = ('<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
                '<d:prop><oc:fileid/><d:resourcetype/></d:prop></d:propfind>')
        root = self.request_xml('PROPFIND', self.file_url(path), body, depth='0')
        response = root.find(D+'response')
        if response is None: raise Stop('fichier_nextcloud_introuvable')
        file_id = ''
        for ps in response.findall(D+'propstat'):
            if ' 200 ' in ps.findtext(D+'status', ''):
                file_id = ps.findtext(D+'prop/'+OC+'fileid', '')
                if file_id: break
        if not re.fullmatch(r'\d+', str(file_id)): raise Stop('identifiant_nextcloud_absent')
        return self.http.base + '/index.php/f/' + str(file_id)

    def inventory(self, path):
        pending, seen, files = [(path, 0)], set(), []
        while pending:
            p, depth = pending.pop()
            if p in seen: continue
            seen.add(p)
            if len(seen) > 100: raise Stop('trop_de_repertoires')
            for item in self.list_folder(p):
                if item['directory']:
                    if depth >= self.cfg.get('max_depth', 8): raise Stop('dossier_trop_profond')
                    pending.append((item['path'], depth+1))
                else:
                    files.append(item)
                    if len(files) > self.cfg.get('max_files', 500): raise Stop('inventaire_trop_volumineux')
        return files

    def inventory_page(self, path, cursor=0, limit=None):
        """Return a deterministic bounded page of a potentially large tree.

        The integer cursor is deliberately opaque to callers.  A page is
        rebuilt from the beginning so no server-side continuation token or
        untrusted DAV href has to be persisted.  Sorting every folder makes a
        retry idempotent as long as the remote tree has not changed.
        """
        path=clean_path(path)
        try:cursor=int(cursor or 0)
        except (TypeError,ValueError):raise Stop('curseur_inventaire_invalide') from None
        if cursor<0:raise Stop('curseur_inventaire_invalide')
        page_limit=int(limit or self.cfg.get('inventory_page_files',250))
        page_limit=max(1,min(page_limit,1000))
        directory_limit=max(100,min(int(self.cfg.get('max_inventory_directories',2000)),10000))
        pending,seen,files,encountered=[(path,0)],set(),[],0
        while pending:
            p,depth=pending.pop(0)
            if p in seen:continue
            seen.add(p)
            if len(seen)>directory_limit:raise Stop('trop_de_repertoires')
            children=sorted(self.list_folder(p),key=lambda item:item['path'])
            directories=[]
            for item in children:
                if item['directory']:
                    if depth>=self.cfg.get('max_depth',8):raise Stop('dossier_trop_profond')
                    directories.append((item['path'],depth+1))
                    continue
                if encountered>=cursor:
                    files.append(item)
                    if len(files)>page_limit:
                        return files[:page_limit],cursor+page_limit,False
                encountered+=1
            pending.extend(directories)
        return files,None,True

    def inventory_step(self,path,state=None):
        """One bounded surveillance batch. Never claim an incomplete tree complete."""
        path=clean_path(path)
        state=state or {'root':path,'pending':[[path,0,0]],'files':{},'visited':0}
        if state.get('root')!=path:raise Stop('racine_surveillance_modifiee')
        pending=list(state['pending']);files=dict(state['files']);visited=state['visited']
        budget=500;folders=0
        while pending and budget>0 and folders<20:
            folder,depth,offset=pending.pop(0)
            if not under(folder,path):raise Stop('surveillance_hors_dossier')
            children=sorted(self.list_folder(folder),key=lambda item:item['path']);folders+=1
            selected=children[offset:offset+budget]
            for item in selected:
                if not under(item['path'],path):raise Stop('surveillance_hors_dossier')
                if item['directory']:
                    if depth>=self.cfg.get('max_depth',8):raise Stop('dossier_trop_profond')
                    pending.append([item['path'],depth+1,0])
                else:files[item['path']]={'etag':item.get('etag',''),'modified':item.get('modified',''),'created':item.get('created',''),'size':item.get('size',0),'error':''}
            budget-=len(selected)
            if offset+len(selected)<len(children):pending.insert(0,[folder,depth,offset+len(selected)])
            else:visited+=1
            if len(files)>100000 or visited>10000:raise Stop('surveillance_limite_securite')
        return {'root':path,'pending':pending,'files':files,'visited':visited},not pending

    def inventory_large(self, path, maximum=None):
        """Inventory a large matter through bounded pages for legal analysis."""
        maximum=int(maximum or self.cfg.get('max_analysis_files',5000))
        maximum=max(1,min(maximum,20000));cursor=0;files=[]
        while True:
            remaining=maximum-len(files)
            if remaining<=0:raise Stop('inventaire_analyse_trop_volumineux')
            page,next_cursor,complete=self.inventory_page(path,cursor,min(500,remaining))
            files.extend(page)
            if complete:return files
            if next_cursor is None or next_cursor<=cursor:raise Stop('curseur_inventaire_invalide')
            cursor=next_cursor

    def download(self, item):
        if item.get('size', 0) > self.cfg.get('max_file_bytes', 15_000_000): raise Stop('piece_trop_volumineuse')
        headers = {'If-Match': item['etag']} if item.get('etag') else {}
        return self.http.request('GET', self.file_url(item['path']), headers=headers,
                                 limit=self.cfg.get('max_file_bytes', 15_000_000))

    def stat(self, path):
        """Return metadata for one file (ETag, size, date, Nextcloud file id)."""
        body = ('<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop>'
                '<d:resourcetype/><d:getetag/><d:creationdate/><d:getlastmodified/><d:getcontentlength/><oc:fileid/></d:prop></d:propfind>')
        root = self.request_xml('PROPFIND', self.file_url(path), body, depth='0')
        response = root.find(D+'response')
        if response is None: raise Stop('fichier_nextcloud_introuvable')
        for ps in response.findall(D+'propstat'):
            if ' 200 ' in ps.findtext(D+'status', ''):
                prop = ps.find(D+'prop')
                if prop.find('.//'+D+'collection') is not None: raise Stop('chemin_est_un_dossier')
                return {'path': clean_path(path), 'etag': prop.findtext(D+'getetag', ''),
                        'modified': prop.findtext(D+'getlastmodified', ''),
                        'created': prop.findtext(D+'creationdate', ''),
                        'size': int(prop.findtext(D+'getcontentlength', '0') or 0),
                        'fileid': prop.findtext(OC+'fileid', '')}
        raise Stop('fichier_nextcloud_introuvable')

    def replace_file(self, path, data, etag):
        """Replace one existing file only if its ETag is still the expected one.

        Nextcloud keeps the previous content as a file version. A concurrent
        change returns HTTP 412 and is surfaced as ``version_nextcloud_modifiee``
        so the caller can save a separate copy instead of overwriting.
        """
        if not isinstance(data, bytes): raise Stop('contenu_fichier_invalide')
        limit = self.cfg.get('max_generated_file_bytes', 20_000_000)
        if not data or len(data) > limit: raise Stop('fichier_genere_trop_volumineux')
        if not etag: raise Stop('etag_nextcloud_absent')
        try:
            self.http.request('PUT', self.file_url(path), data,
                              {'Content-Type': 'application/octet-stream', 'If-Match': etag}, limit + 1)
        except Stop as ex:
            if str(ex) == 'http_412': raise Stop('version_nextcloud_modifiee') from None
            raise
        return path

    def create_folder(self,path):
        path=clean_path(path)
        if path in {clean_path(x) for x in self.cfg['roots']}:raise Stop('creation_racine_refusee')
        if not any(under(path,root) for root in self.cfg['roots']):raise Stop('fichier_hors_racines')
        parent=str(PurePosixPath(path).parent)
        self.list_folder(parent)
        self.http.request('MKCOL',self.file_url(path),b'',{'If-None-Match':'*'},100000)
        return path

    def ensure_folder(self, path, boundary):
        """Create missing descendants only, never outside the selected matter."""
        path,boundary=clean_path(path),clean_path(boundary)
        if not under(path,boundary):raise Stop('destination_hors_dossier')
        current=boundary
        self.list_folder(current)
        relative=PurePosixPath(path).relative_to(PurePosixPath(boundary))
        for part in relative.parts:
            current=clean_path(current+'/'+part)
            try:self.list_folder(current)
            except Stop as ex:
                if str(ex)!='http_404':raise
                self.http.request('MKCOL',self.file_url(current),b'',{'If-None-Match':'*'},100000)
        return path

    def put_file(self, path, data, content_type='application/octet-stream'):
        """Create one new file exclusively. Existing files are never replaced."""
        if not isinstance(data,bytes):raise Stop('contenu_fichier_invalide')
        limit=self.cfg.get('max_generated_file_bytes',20_000_000)
        if not data or len(data)>limit:raise Stop('fichier_genere_trop_volumineux')
        self.http.request('PUT',self.file_url(path),data,
          {'Content-Type':content_type,'If-None-Match':'*'},limit+1)
        return path

    def trash_file(self, path, etag):
        """5.6.24 : suppression conditionnelle (If-Match) d'un fichier ; Nextcloud le conserve dans sa corbeille (restaurable)."""
        if not etag: raise Stop('etag_nextcloud_absent')
        self.http.request('DELETE', self.file_url(path), None, {'If-Match': etag}, 100000)
        return path

    def calendars(self):
        body = ('<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                '<d:prop><d:displayname/><d:resourcetype/>'
                '<c:supported-calendar-component-set/></d:prop></d:propfind>')
        root = self.request_xml('PROPFIND', self.calendar_home, body)
        out = []
        for r in root.findall(D+'response'):
            for ps in r.findall(D+'propstat'):
                if ' 200 ' not in ps.findtext(D+'status', ''): continue
                p = ps.find(D+'prop')
                if p is not None and p.find('.//'+C+'calendar') is not None:
                    href = r.findtext(D+'href', '')
                    components=[x.get('name','').upper() for x in
                                p.findall('.//'+C+'supported-calendar-component-set/'+C+'comp')]
                    out.append({'name': p.findtext(D+'displayname', ''),
                                'url': self.calendar_url(href),
                                'components':components or ['VEVENT','VTODO']})
        return out

    def calendar_url(self, href):
        url = U.urljoin(self.http.base + '/', href)
        p, h = U.urlsplit(url), U.urlsplit(self.calendar_home)
        # Only own/shared calendars visible beneath this authenticated home.
        if (p.scheme, p.netloc) != (h.scheme, h.netloc) or not p.path.startswith(h.path) or p.query or p.fragment:
            raise Stop('calendrier_hors_compte')
        if '..' in U.unquote(p.path).split('/'): raise Stop('calendrier_invalide')
        return url.rstrip('/') + '/'

    def events(self, urls, start, end, tz, include_cancelled=False):
        if not urls: raise Stop('agenda_non_configure')
        fmt = lambda d: d.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        a, b = fmt(start), fmt(end)
        body = ('<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                '<d:prop><d:getetag/><c:calendar-data><c:expand start="'+a+'" end="'+b+'"/>'
                '</c:calendar-data></d:prop><c:filter><c:comp-filter name="VCALENDAR">'
                '<c:comp-filter name="VEVENT"><c:time-range start="'+a+'" end="'+b+'"/>'
                '</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>')
        out = []
        for url in urls:
            root = self.request_xml('REPORT', self.calendar_url(url), body)
            for response in root.findall(D+'response'):
                ok = False
                for ps in response.findall(D+'propstat'):
                    if ' 200 ' not in ps.findtext(D+'status', ''): continue
                    data = ps.findtext('.//'+C+'calendar-data')
                    if data is not None:
                        href=response.findtext(D+'href','')
                        etag=ps.findtext('.//'+D+'getetag','')
                        for event in parse_events(data, tz,include_cancelled):
                            event.update(source_url=self.calendar_url(url),href=href,etag=etag)
                            out.append(event)
                        ok = True
                if not ok: raise Stop('evenement_agenda_non_lisible')
        return out

    def todos(self, urls, tz, limit=1000):
        """Read VTODO objects from selected Nextcloud task calendars.

        The query is deliberately bounded and does not mutate remote objects.
        Nextcloud Tasks stores its lists as CalDAV calendars.
        """
        if not urls: raise Stop('liste_taches_non_configuree')
        body = ('<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                '<d:prop><d:getetag/><c:calendar-data/></d:prop>'
                '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VTODO"/>'
                '</c:comp-filter></c:filter></c:calendar-query>')
        out=[];limit=max(1,min(int(limit),2000))
        for url in urls:
            root=self.request_xml('REPORT',self.calendar_url(url),body)
            for response in root.findall(D+'response'):
                for ps in response.findall(D+'propstat'):
                    if ' 200 ' not in ps.findtext(D+'status',''):continue
                    data=ps.findtext('.//'+C+'calendar-data')
                    if data is None:continue
                    href=response.findtext(D+'href','');etag=ps.findtext('.//'+D+'getetag','')
                    for task in parse_todos(data,tz):
                        task.update(source_url=self.calendar_url(url),href=href,etag=etag)
                        out.append(task)
                        if len(out)>=limit:return out
        return out

    def put_todo(self, calendar, uid, title, description='', start=None, due=None,
                 status='NEEDS-ACTION', priority=5, percent=0, etag=''):
        if not re.fullmatch(r'axiorhub-[0-9a-f]{32,64}@mail-agent\.local',uid):
            raise Stop('identifiant_tache_invalide')
        if not title or len(title)>500 or any(x in title for x in ('\r','\n')):
            raise Stop('titre_tache_invalide')
        if len(description)>5000:raise Stop('description_tache_invalide')
        if status not in {'NEEDS-ACTION','IN-PROCESS','COMPLETED','CANCELLED'}:
            raise Stop('etat_tache_invalide')
        priority=max(0,min(int(priority),9));percent=max(0,min(int(percent),100))
        def esc(value):return str(value).replace('\\','\\\\').replace(';','\\;').replace(',','\\,').replace('\n','\\n').replace('\r','')
        def fmt(value):
            if not value:return ''
            stamp=value if isinstance(value,datetime) else datetime.fromisoformat(str(value))
            if not stamp.tzinfo:stamp=stamp.replace(tzinfo=timezone.utc)
            return stamp.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//AxiorHub//Mail Agent 4.2.0//FR',
               'BEGIN:VTODO','UID:'+uid,'DTSTAMP:'+stamp,'LAST-MODIFIED:'+stamp,
               'SUMMARY:'+esc(title),'DESCRIPTION:'+esc(description),'STATUS:'+status,
               'PRIORITY:'+str(priority),'PERCENT-COMPLETE:'+str(percent)]
        if start:lines.append('DTSTART:'+fmt(start))
        if due:lines.append('DUE:'+fmt(due))
        if status=='COMPLETED':lines.append('COMPLETED:'+stamp)
        lines+=['END:VTODO','END:VCALENDAR','']
        url=self.calendar_url(calendar)+U.quote(uid,safe='')+'.ics'
        headers={'Content-Type':'text/calendar; charset=utf-8',
                 'If-Match':etag} if etag else {
                 'Content-Type':'text/calendar; charset=utf-8','If-None-Match':'*'}
        self.http.request('PUT',url,'\r\n'.join(lines).encode(),headers,100000)
        return {'uid':uid,'url':url,'status':status}

    def delete_event(self, calendar, uid, etag=''):
        """Supprime un événement créé par AxiorHub (identifiant axiorhub-<64 hex>) ; jamais un événement d'un tiers."""
        if not re.fullmatch(r'axiorhub-[0-9a-f]{64}@mail-agent\.local',uid):raise Stop('evenement_externe_lecture_seule')
        url=self.calendar_url(calendar)+U.quote(uid,safe='')+'.ics'
        self.http.request('DELETE',url,None,{'If-Match':etag} if etag else {},100000)
        return uid

    def put_event(self, calendar, proposal_id, title, start, end, description, replace=False):
        if not re.fullmatch(r'[0-9a-f]{64}',proposal_id):raise Stop('identifiant_evenement_invalide')
        if not title or len(title)>500 or any(x in title for x in ('\r','\n')):raise Stop('titre_evenement_invalide')
        if len(description)>2000:raise Stop('description_evenement_invalide')
        def esc(value):return value.replace('\\','\\\\').replace(';','\\;').replace(',','\\,').replace('\n','\\n').replace('\r','')
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        fmt=lambda d:d.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        uid='axiorhub-'+proposal_id+'@mail-agent.local'
        data=('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//AxiorHub//Mail Agent 4.2.0//FR\r\n'
              'BEGIN:VEVENT\r\nUID:'+uid+'\r\nDTSTAMP:'+stamp+'\r\nDTSTART:'+fmt(start)+'\r\nDTEND:'+fmt(end)+'\r\n'
              'SUMMARY:'+esc(title)+'\r\nDESCRIPTION:'+esc(description)+'\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n').encode()
        url=self.calendar_url(calendar)+U.quote(uid,safe='')+'.ics'
        condition='If-Match' if replace else 'If-None-Match'
        self.http.request('PUT',url,data,{'Content-Type':'text/calendar; charset=utf-8',condition:'*'},100000)
        return uid


def parse_ical_date(key, value, tz):
    params = dict(p.split('=', 1) for p in key.split(';')[1:] if '=' in p)
    zone = ZoneInfo(params.get('TZID', tz).strip('"'))
    try:
        if re.fullmatch(r'\d{8}', value):
            return datetime.strptime(value, '%Y%m%d').replace(tzinfo=zone), True
        if value.endswith('Z'):
            return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc), False
        return datetime.strptime(value, '%Y%m%dT%H%M%S').replace(tzinfo=zone), False
    except (ValueError, KeyError): raise Stop('date_agenda_non_lisible') from None


def parse_events(ics, tz, include_cancelled=False):
    lines = re.sub(r'\r?\n[ \t]', '', ics).splitlines()
    events, fields, depth = [], None, 0
    for line in lines:
        if line == 'BEGIN:VEVENT': fields, depth = {}, 0; continue
        if fields is None: continue
        if line == 'END:VEVENT':
            # The CalDAV REPORT asks Nextcloud to expand recurrence. If a
            # server returns the recurring master unchanged, treating it as one
            # occurrence would silently hide later audiences: fail closed.
            if 'RRULE' in fields or 'RDATE' in fields:raise Stop('recurrence_non_developpee')
            if fields.get('STATUS', ('', ''))[1] == 'CANCELLED':
                if not include_cancelled:fields=None;continue
                if 'DTSTART' not in fields:
                    events.append({'uid':fields.get('UID',('',''))[1],'recurrence_id':fields.get('RECURRENCE-ID',('',''))[1],
                                   'summary':fields.get('SUMMARY',('',''))[1],'status':'CANCELLED','start':'','end':'','busy':False})
                    fields=None;continue
            if 'DTSTART' not in fields: raise Stop('debut_evenement_absent')
            start, all_day = parse_ical_date(*fields['DTSTART'], tz)
            if 'DTEND' in fields: end, _ = parse_ical_date(*fields['DTEND'], tz)
            elif 'DURATION' in fields:
                m = re.fullmatch(r'P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?', fields['DURATION'][1])
                if not m: raise Stop('duree_agenda_non_lisible')
                w, d, h, minute, sec = [int(x or 0) for x in m.groups()]
                end = start + timedelta(weeks=w, days=d, hours=h, minutes=minute, seconds=sec)
            elif all_day: end = start + timedelta(days=1)
            else: raise Stop('fin_evenement_absente')
            if end <= start: raise Stop('duree_agenda_invalide')
            value = lambda k: fields.get(k, ('', ''))[1].replace('\\n', '\n').replace('\\,', ',').replace('\\;', ';')
            events.append({'uid': value('UID'), 'start': start.isoformat(), 'end': end.isoformat(),
                           'summary': value('SUMMARY'), 'description': value('DESCRIPTION'), 'all_day':all_day,
                           'location':value('LOCATION'),'status':value('STATUS') or 'CONFIRMED',
                           'recurrence_id':value('RECURRENCE-ID'),
                           'busy': value('TRANSP') != 'TRANSPARENT'})
            fields = None
            continue
        if line.startswith('BEGIN:'): depth += 1; continue
        if line.startswith('END:'): depth -= 1; continue
        if depth == 0 and ':' in line:
            key, value = line.split(':', 1)
            fields[key.split(';')[0]] = (key, value)
    if fields is not None: raise Stop('ics_incomplet')
    return events


def parse_todos(ics,tz):
    lines=re.sub(r'\r?\n[ \t]','',ics).splitlines();tasks=[];fields=None;depth=0
    for line in lines:
        if line=='BEGIN:VTODO':fields={};depth=0;continue
        if fields is None:continue
        if line=='END:VTODO':
            value=lambda k:fields.get(k,('',''))[1].replace('\\n','\n').replace('\\,',',').replace('\\;',';')
            uid=value('UID');title=value('SUMMARY')
            if not uid or not title:raise Stop('tache_caldav_incomplete')
            def optional_date(name):
                if name not in fields:return ''
                return parse_ical_date(*fields[name],tz)[0].isoformat()
            status=value('STATUS') or 'NEEDS-ACTION'
            try:priority=int(value('PRIORITY') or 0);percent=int(value('PERCENT-COMPLETE') or 0)
            except ValueError:raise Stop('tache_caldav_invalide') from None
            tasks.append({'uid':uid,'title':title,'description':value('DESCRIPTION'),
              'start':optional_date('DTSTART'),'due':optional_date('DUE'),
              'completed':optional_date('COMPLETED'),'status':status,
              'priority':priority,'percent':percent})
            fields=None;continue
        if line.startswith('BEGIN:'):depth+=1;continue
        if line.startswith('END:'):depth-=1;continue
        if depth==0 and ':' in line:
            key,value=line.split(':',1);fields[key.split(';')[0]]=(key,value)
    if fields is not None:raise Stop('ics_incomplet')
    return tasks


def available_slots(events, now, cfg):
    zone = ZoneInfo(cfg.get('timezone', 'Europe/Paris'))
    now = now.astimezone(zone)
    duration = timedelta(minutes=cfg.get('appointment_minutes', 30))
    margin = timedelta(minutes=cfg.get('buffer_minutes', 15))
    earliest = now + timedelta(hours=cfg.get('notice_hours', 24))
    busy = [(datetime.fromisoformat(e['start'])-margin, datetime.fromisoformat(e['end'])+margin)
            for e in events if e['busy']]
    out = []
    for offset in range(cfg.get('availability_days', 10)):
        day = now + timedelta(days=offset)
        if day.weekday() not in cfg.get('weekdays', [0, 1, 2, 3, 4]): continue
        daily = []
        for a, b in cfg.get('working_hours', [['09:00', '12:00'], ['14:00', '18:00']]):
            h, m = map(int, a.split(':')); cursor = day.replace(hour=h, minute=m, second=0, microsecond=0)
            h, m = map(int, b.split(':')); end = day.replace(hour=h, minute=m, second=0, microsecond=0)
            while cursor+duration <= end:
                if cursor >= earliest and not any(cursor < y and cursor+duration > x for x, y in busy):
                    daily.append({'id': 'slot-' + cursor.strftime('%Y%m%dT%H%M'),
                                  'start': cursor.isoformat(), 'end': (cursor+duration).isoformat()})
                cursor += timedelta(minutes=30)
        if daily: out.append(daily[0])
        if len(out) == 3: break
    return out


def document_sources(dav, matter, terms, cfg):
    inventory = dav.inventory_large(matter['path']) if hasattr(dav,'inventory_large') else dav.inventory(matter['path'])
    tokens = {t for term in terms for t in re.findall(r'[a-z0-9]{3,}', fold(term))}
    def score(item):
        path = fold(item['path'])
        return sum(4 for t in tokens if t in path) + (1 if 'fiche' in path or 'etat' in path else 0)
    inventory.sort(key=lambda x: (-score(x), x['path']))
    max_files = cfg.get('max_documents_per_mail', 7)
    # Inspect bounded text from the selected matter, never the whole cabinet.
    sources, failed = [], []
    for item in inventory[:max_files]:
        try:
            raw = dav.download(item)
            text = extract(raw, item['path'], cfg)
            chunks = [text[i:i+6500] for i in range(0, len(text), 6000)]
            ranked = sorted(enumerate(chunks), key=lambda x: -sum(fold(x[1]).count(t) for t in tokens))[:2]
            excerpt = '\n\n'.join('[Extrait '+str(i+1)+']\n'+chunk for i, chunk in sorted(ranked))
            sources.append({'id': 'doc-' + digest(item['path'] + item['etag'])[:16],
                            'kind': 'document', 'path': item['path'], 'etag': item['etag'],
                            'modified': item['modified'], 'excerpt': excerpt,
                            'partial': len(chunks) > len(ranked)})
        except Stop as e:
            failed.append({'path': item['path'], 'reason': str(e)})
    return sources, {'total_files': len(inventory), 'examined_files': min(len(inventory), max_files),
                     'not_exhaustive': len(inventory) > max_files, 'failed': failed}


EXCLUDED_NAMES = {'secrets', 'mots de passe'}


class LocalFolder(DAV):
    """5.6.22 : dossier de travail local avec l'interface du client WebDAV.

    ``cfg['local_path']`` est la racine locale ; les chemins du cabinet (racines ``/Dossiers``, chemins des dossiers) sont
    résolus sous cette racine, sans jamais en sortir. Les fichiers et dossiers commençant par un point, les répertoires
    « secrets » et les liens symboliques sont ignorés comme avec Nextcloud. Les agendas (CalDAV) exigent ``url``, ``username``
    et ``password_file`` ; sinon ils sont signalés indisponibles plutôt que simulés.

    5.6.24 (F10 à F13) :
    - chaque composant du chemin est contrôlé sans suivre les liens (lstat) : un alias vers « secrets », vers un autre dossier
      ou vers l'extérieur est refusé au listage, au stat, au téléchargement, à la création et au remplacement ; la lecture passe
      par un descripteur ouvert avec O_NOFOLLOW quand le système le propose ;
    - l'ETag est une empreinte SHA-256 du contenu (mise en cache par identité de fichier, date en nanosecondes et taille) ; le
      téléchargement compare l'empreinte des octets réellement lus et vérifie que le fichier n'a pas changé pendant la lecture ;
    - la création est une publication exclusive (lien dur depuis un fichier temporaire du même volume, sinon O_EXCL) : jamais de
      remplacement d'un fichier apparu entre le contrôle et l'écriture ;
    - le remplacement se fait sous verrou, après relecture de la version attendue ; chaque version précédente reçoit un nom unique
      (horodatage à la microseconde, empreinte, jeton) publié exclusivement ; la rétention ``local_versions_keep`` (défaut 100,
      0 = illimitée) est administrable dans la configuration ``nextcloud``.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.local = Path(str(cfg['local_path'])).expanduser().resolve()
        if not self.local.is_dir():
            raise Stop('dossier_local_introuvable')
        self.refused = []
        self.http = None
        self._hashes = {}
        if cfg.get('url') and cfg.get('username') and cfg.get('password_file'):
            self.http = HTTP(cfg['url'], cfg['username'], read_secret(cfg['password_file']))
            self.files = self.http.base + '/remote.php/dav/files/' + U.quote(cfg['username'], safe='')
            self.calendar_home = self.http.base + '/remote.php/dav/calendars/' + U.quote(cfg['username'], safe='') + '/'

    # ----- chemins
    def _check(self, path):
        path = clean_path(path)
        if not any(under(path, root) for root in self.cfg['roots']): raise Stop('fichier_hors_racines')
        if any(p.startswith('.') or fold(p) in EXCLUDED_NAMES for p in path.split('/') if p): raise Stop('repertoire_exclu')
        return path

    def _fs(self, path):
        """Chemin métier contrôlé et chemin local correspondant. Aucun composant ne peut être un lien symbolique (F12) ni « .. »."""
        path = self._check(path)
        target = self.local
        for part in [x for x in path.split('/') if x]:
            if part in ('.', '..'): raise Stop('chemin_refuse')
            target = target / part
            if target.is_symlink(): raise Stop('lien_symbolique_refuse')
        resolved = target.resolve()
        if resolved != self.local and self.local not in resolved.parents: raise Stop('chemin_refuse')
        return path, target

    @staticmethod
    def _stamp(seconds):
        return format_datetime(datetime.fromtimestamp(seconds, timezone.utc), usegmt=True)

    @staticmethod
    def _mtime_ns(st):
        return getattr(st, 'st_mtime_ns', None) or int(st.st_mtime * 1_000_000_000)

    @staticmethod
    def _etag_bytes(data):
        return '"%s"' % hashlib.sha256(data).hexdigest()[:32]

    @staticmethod
    def _open_read(target):
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        try:
            return os.open(str(target), flags)
        except FileNotFoundError:
            raise Stop('http_404') from None
        except OSError as ex:
            if getattr(ex, 'errno', None) == errno.ELOOP: raise Stop('lien_symbolique_refuse') from None
            raise Stop('fichier_illisible') from None

    def _read(self, target, limit):
        """Octets d'un fichier lus par descripteur ; l'état (taille, date) est comparé avant et après la lecture (F13)."""
        fd = self._open_read(target)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode): raise Stop('http_404')
            if before.st_size > limit: raise Stop('reponse_trop_volumineuse')
            chunks = []
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk: break
                chunks.append(chunk)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        if (before.st_size, self._mtime_ns(before)) != (after.st_size, self._mtime_ns(after)): raise Stop('fichier_modifie_pendant_lecture')
        return b''.join(chunks), before

    def _etag(self, target, st=None):
        """Empreinte SHA-256 du contenu, en cache par identité de fichier, date en nanosecondes et taille (F13)."""
        st = st or target.stat()
        key = (str(target), getattr(st, 'st_ino', 0), self._mtime_ns(st), st.st_size)
        if key not in self._hashes:
            fd = self._open_read(target)
            h = hashlib.sha256()
            try:
                while True:
                    chunk = os.read(fd, 1 << 20)
                    if not chunk: break
                    h.update(chunk)
            finally:
                os.close(fd)
            if len(self._hashes) > 5000: self._hashes.clear()
            self._hashes[key] = '"%s"' % h.hexdigest()[:32]
        return self._hashes[key]

    def _item(self, path, target):
        st = target.stat()
        return {'path': path, 'directory': target.is_dir(), 'etag': self._etag(target, st) if target.is_file() else '',
                'modified': self._stamp(st.st_mtime), 'created': self._stamp(getattr(st, 'st_birthtime', None) or st.st_ctime), 'size': st.st_size if target.is_file() else 0}

    # ----- lecture
    def list_folder(self, path):
        path, target = self._fs(path)
        if not target.exists(): raise Stop('http_404')
        if not target.is_dir(): raise Stop('chemin_est_un_fichier')
        out = []
        for child in sorted(target.iterdir(), key=lambda c: c.name):
            if child.is_symlink():
                self.refused.append(child.name[-120:]); continue
            if child.name.startswith('.') or fold(child.name) in EXCLUDED_NAMES: continue
            if not (child.is_dir() or child.is_file()): continue
            out.append(self._item(clean_path(path + '/' + child.name), child))
        return out

    def file_web_url(self, path):
        _, target = self._fs(path)
        if not target.is_file(): raise Stop('fichier_nextcloud_introuvable')
        return target.as_uri()

    def download(self, item):
        limit = self.cfg.get('max_file_bytes', 15_000_000)
        if item.get('size', 0) > limit: raise Stop('piece_trop_volumineuse')
        _, target = self._fs(item['path'])
        if not target.is_file(): raise Stop('http_404')
        data, _ = self._read(target, limit)
        if item.get('etag') and item['etag'] != self._etag_bytes(data): raise Stop('http_412')   # F13 : comparaison sur les octets lus
        return data

    def stat(self, path):
        path, target = self._fs(path)
        if not target.exists(): raise Stop('fichier_nextcloud_introuvable')
        if target.is_dir(): raise Stop('chemin_est_un_dossier')
        return {**{k: v for k, v in self._item(path, target).items() if k != 'directory'}, 'fileid': ''}

    # ----- écriture (jamais hors des racines, jamais de remplacement silencieux)
    @staticmethod
    def _sync_dir(directory):
        try:
            fd = os.open(str(directory), os.O_RDONLY)
        except OSError:
            return
        try: os.fsync(fd)
        except OSError: pass
        finally: os.close(fd)

    def _tmp(self, directory, data):
        fd, tmp = tempfile.mkstemp(dir=str(directory), prefix='.axiorhub-')
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        return tmp

    def _publish(self, target, data):
        """F10 : publication exclusive — lien dur depuis le fichier temporaire (même volume), sinon création O_EXCL. Un fichier
        apparu après le contrôle initial fait échouer la création (http_412) au lieu d'être remplacé."""
        if target.is_symlink(): raise Stop('lien_symbolique_refuse')
        tmp = self._tmp(target.parent, data)
        try:
            try:
                os.link(tmp, str(target))
            except FileExistsError:
                raise Stop('http_412') from None
            except (AttributeError, NotImplementedError, OSError) as ex:
                if getattr(ex, 'errno', None) == errno.EEXIST: raise Stop('http_412') from None
                try:
                    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o644)
                except FileExistsError:
                    raise Stop('http_412') from None
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(data); handle.flush(); os.fsync(handle.fileno())
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        self._sync_dir(target.parent)

    @contextmanager
    def _lock(self, target):
        """Verrou de remplacement (fichier exclusif à côté de la cible) ; un verrou abandonné depuis plus de deux minutes est repris."""
        lock = target.parent / ('.axiorhub-lock-' + target.name)
        for _ in range(500):
            try:
                os.close(os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL)); break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 120: lock.unlink()
                    else: time.sleep(0.01)
                except OSError:
                    pass
        else:
            raise Stop('fichier_verrouille')
        try:
            yield
        finally:
            try: lock.unlink()
            except OSError: pass

    def _prune_versions(self, versions, name):
        keep = int(self.cfg.get('local_versions_keep', 100) or 0)
        if keep <= 0: return
        copies = sorted(c.name for c in versions.iterdir() if c.is_file() and c.name.startswith(name + '.'))
        for old in copies[:-keep] if len(copies) > keep else []:
            try: (versions / old).unlink()
            except OSError: pass

    def replace_file(self, path, data, etag):
        if not isinstance(data, bytes): raise Stop('contenu_fichier_invalide')
        limit = self.cfg.get('max_generated_file_bytes', 20_000_000)
        if not data or len(data) > limit: raise Stop('fichier_genere_trop_volumineux')
        if not etag: raise Stop('etag_nextcloud_absent')
        path, target = self._fs(path)
        if not target.is_file(): raise Stop('fichier_nextcloud_introuvable')
        with self._lock(target):
            current, _ = self._read(target, max(limit, self.cfg.get('max_file_bytes', 15_000_000)))
            if self._etag_bytes(current) != etag: raise Stop('version_nextcloud_modifiee')   # F13 : version relue sous verrou, juste avant publication
            versions = target.parent / '.axiorhub-versions'
            if versions.is_symlink(): raise Stop('lien_symbolique_refuse')
            versions.mkdir(exist_ok=True)
            name = '%s.%s.%s.%s' % (target.name, datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'), hashlib.sha256(current).hexdigest()[:12], secrets.token_hex(4))
            self._publish(versions / name, current)   # F11 : copie unique, publiée exclusivement, jamais écrasée
            tmp = self._tmp(target.parent, data)
            os.replace(tmp, target)                     # remplacement autorisé : sous verrou, version vérifiée, copie conservée
            self._sync_dir(target.parent); self._sync_dir(versions)
            self._prune_versions(versions, target.name)
        return path

    def create_folder(self, path):
        path = clean_path(path)
        if path in {clean_path(x) for x in self.cfg['roots']}: raise Stop('creation_racine_refusee')
        path, target = self._fs(path)
        self.list_folder(str(PurePosixPath(path).parent))
        try:
            target.mkdir()
        except FileExistsError:
            raise Stop('http_412') from None
        return path

    def ensure_folder(self, path, boundary):
        path, boundary = clean_path(path), clean_path(boundary)
        if not under(path, boundary): raise Stop('destination_hors_dossier')
        current = boundary
        self.list_folder(current)
        for part in PurePosixPath(path).relative_to(PurePosixPath(boundary)).parts:
            current = clean_path(current + '/' + part)
            try: self.list_folder(current)
            except Stop as ex:
                if str(ex) != 'http_404': raise
                try: self._fs(current)[1].mkdir()
                except FileExistsError: pass
        return path

    def trash_file(self, path, etag):
        """5.6.24 : déplacement dans ``.axiorhub-corbeille`` du même dossier (nom unique, restaurable), après contrôle de la version."""
        path, target = self._fs(path)
        if not target.is_file(): raise Stop('fichier_nextcloud_introuvable')
        with self._lock(target):
            current, _ = self._read(target, max(self.cfg.get('max_generated_file_bytes', 20_000_000), self.cfg.get('max_file_bytes', 15_000_000)))
            if not etag or self._etag_bytes(current) != etag: raise Stop('version_nextcloud_modifiee')
            bin_dir = target.parent / '.axiorhub-corbeille'
            if bin_dir.is_symlink(): raise Stop('lien_symbolique_refuse')
            bin_dir.mkdir(exist_ok=True)
            self._publish(bin_dir / ('%s.%s.%s' % (target.name, datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'), secrets.token_hex(4))), current)
            os.unlink(str(target))
            self._sync_dir(target.parent)
        return path

    def put_file(self, path, data, content_type='application/octet-stream'):
        if not isinstance(data, bytes): raise Stop('contenu_fichier_invalide')
        limit = self.cfg.get('max_generated_file_bytes', 20_000_000)
        if not data or len(data) > limit: raise Stop('fichier_genere_trop_volumineux')
        path, target = self._fs(path)
        if target.exists() or target.is_symlink(): raise Stop('http_412')
        if not target.parent.is_dir(): raise Stop('http_409')
        self._publish(target, data)                     # F10 : exclusif, même si le fichier est apparu entre-temps
        return path

    # ----- agendas : CalDAV seulement
    def _caldav(self):
        if self.http is None: raise Stop('agenda_webdav_non_configure')

    def calendars(self):
        self._caldav(); return DAV.calendars(self)

    def events(self, *args, **kwargs):
        self._caldav(); return DAV.events(self, *args, **kwargs)

    def todos(self, *args, **kwargs):
        self._caldav(); return DAV.todos(self, *args, **kwargs)

    def put_todo(self, *args, **kwargs):
        self._caldav(); return DAV.put_todo(self, *args, **kwargs)

    def put_event(self, *args, **kwargs):
        self._caldav(); return DAV.put_event(self, *args, **kwargs)

    def delete_event(self, *args, **kwargs):
        self._caldav(); return DAV.delete_event(self, *args, **kwargs)

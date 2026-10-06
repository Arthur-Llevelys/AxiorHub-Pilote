"""Veille RSS/Atom officielle : données datées, pas de jurisprudence inventée."""
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
import hashlib
from html import unescape
import json
import re
from urllib.parse import urlsplit, urljoin

from .common import HTTP, Stop, fold, xml_bytes
from . import settings568

DOMAINS=('legifrance.gouv.fr','courdecassation.fr','conseil-etat.fr','justice.gouv.fr',
         'service-public.gouv.fr','entreprendre.service-public.gouv.fr','eur-lex.europa.eu','curia.europa.eu',
         'cnil.fr','economie.gouv.fr','travail-emploi.gouv.fr')


def validate_source(url):
    p=urlsplit(str(url))
    try:port=p.port
    except ValueError:raise Stop('source_veille_invalide') from None
    if p.scheme!='https' or p.username or p.password or p.fragment or port not in (None,443) or not p.hostname or not any(p.hostname==d or p.hostname.endswith('.'+d) for d in DOMAINS):raise Stop('source_veille_officielle_requise')
    if len(url)>600 or any(ord(c)<32 for c in url):raise Stop('source_veille_invalide')
    return url


def clean(text,limit=4000):
    return re.sub(r'\s+',' ',unescape(re.sub(r'<[^>]+>',' ',str(text)))).strip()[:limit]


def parse(raw,source,now=None):
    now=now or datetime.now(timezone.utc);root=xml_bytes(raw)
    rows=root.findall('.//item') or root.findall('{http://www.w3.org/2005/Atom}entry')
    if not rows and root.tag not in ('rss','{http://www.w3.org/2005/Atom}feed'):raise Stop('flux_veille_rss_atom_requis')
    out=[];a='{http://www.w3.org/2005/Atom}'
    for r in rows[:200]:
        title=clean(r.findtext('title') or r.findtext(a+'title'),600)
        link=r.findtext('link') or ''
        if not link:
            link=next((x.get('href','') for x in r.findall(a+'link') if x.get('rel','alternate')=='alternate'),'')
        url=urljoin(source,link)
        try:validate_source(url)
        except Stop:continue
        when=r.findtext('pubDate') or r.findtext(a+'published') or r.findtext(a+'updated') or r.findtext('{http://purl.org/dc/elements/1.1/}date')
        try:
            date=parsedate_to_datetime(when) if when and ',' in when else datetime.fromisoformat(str(when).replace('Z','+00:00'))
            if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
        except (ValueError,TypeError,OverflowError):continue
        if not now-timedelta(days=14)<=date<=now+timedelta(hours=1) or not title:continue
        summary=clean(r.findtext('description') or r.findtext(a+'summary') or r.findtext(a+'content') or '',4000)
        out.append({'url':url,'title':title,'published':date.astimezone(timezone.utc).isoformat(),'summary':summary})
    return out


def collect(desk,owner='cabinet',fetch=None):
    p=settings568.profile(desk,owner)
    if not p['news_enabled'] or not p['legal_fields'] or not p['news_sources']:return {'abstention':'Renseignez les domaines et flux officiels de veille.'}
    if 'A6' not in p['roles']:return {'abstention':'Rôle veille désactivé.'}
    settings568.check_owner(desk,owner)
    added=0;errors=[];source_ok=0
    for source in p['news_sources']:
        try:
            validate_source(source)
            raw=fetch(source) if fetch else HTTP('https://'+urlsplit(source).netloc,timeout=20).request('GET',source,limit=2_000_000)
            rows=parse(raw,source);source_ok+=1
            for r in rows:
                content=fold(r['title']+' '+r['summary']);fields=[f for f in p['legal_fields'] if fold(f) in content]
                if not fields:continue
                nid=hashlib.sha256(json.dumps([owner,r['url'],r['published']],ensure_ascii=False).encode()).hexdigest()
                cur=desk.db.execute('INSERT OR IGNORE INTO news_v568 VALUES(?,?,?,?,?,?,?,?,?)',(nid,owner,source,r['url'],r['title'],r['published'],r['summary'],json.dumps(fields,ensure_ascii=False),desk.now()));desk.db.commit();added+=cur.rowcount
            desk.setting('proactive568:news_source:'+owner+':'+hashlib.sha256(source.encode()).hexdigest(),{'checked':desk.now(),'items':len(rows),'ok':True})
        except Stop as exc:
            errors.append({'source':source,'reason':str(exc)})
    desk.setting('proactive568:news_result:'+owner,{'at':desk.now(),'sources_ok':source_ok,'added':added,'errors':errors})
    desk.db.execute('DELETE FROM news_v568 WHERE owner=? AND detected<?',(owner,(datetime.now(timezone.utc)-timedelta(days=p['retention_days'])).isoformat()));desk.db.commit()
    if errors and not source_ok:raise Stop('veille_toutes_sources_indisponibles')
    return {'added':added,'sources_checked':source_ok,'errors':errors,'verification':'Métadonnées et extrait du flux officiel ; lire la décision complète avant utilisation juridique.'}


def listing(desk,owner='cabinet'):
    rows=[dict(r) for r in desk.db.execute('SELECT * FROM news_v568 WHERE owner=? ORDER BY published DESC LIMIT 30',(owner,)).fetchall()]
    return {'items':rows,'last_run':desk.settings('proactive568:news_result:'+owner,{}),'verification_scope':'Flux officiel daté, pas validation du raisonnement juridique.'}

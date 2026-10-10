"""Legal research, official verification and immutable provenance for AxiorHub 2.4.0.

External providers receive only a deterministically anonymised legal question.
Provider hits are leads.  A decision becomes citable only after AxiorHub has
downloaded an official page, matched its identifier and stored the exact quote
with the digest of the official text.
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import hashlib
import json
from pathlib import PurePosixPath
import re
import unicodedata
from urllib.parse import urlsplit

from .common import HTTP, Stop, clean_path, digest, fold, load_matters, read_secret
from .documents import extract


OFFICIAL_HOSTS=(
  'legifrance.gouv.fr','www.legifrance.gouv.fr',
  'courdecassation.fr','www.courdecassation.fr',
  'judilibre.courdecassation.fr','justice.fr','www.justice.fr',
  'conseil-etat.fr','www.conseil-etat.fr',
  'conseil-constitutionnel.fr','www.conseil-constitutionnel.fr',
  'eur-lex.europa.eu','curia.europa.eu',
)
PROVIDERS=('openlegal','openlegi','goodlegal','pappers')
WRITING_TYPES={
  'conclusions':('conclusion','conclusions'),
  'assignation':('assignation',),
  'bcp':('bordereau','bcp','communication de pieces'),
  'courrier':('courrier','lettre'),
  'document':(),
}

SCHEMAS=('''
CREATE TABLE IF NOT EXISTS latest_writings_v240(
  matter TEXT NOT NULL, document_type TEXT NOT NULL, path TEXT NOT NULL,
  etag TEXT NOT NULL, modified TEXT NOT NULL, sha256 TEXT NOT NULL,
  detected_date TEXT NOT NULL, detected_version INTEGER NOT NULL,
  writing_status TEXT NOT NULL, score TEXT NOT NULL, selected INTEGER NOT NULL,
  certainty TEXT NOT NULL, reason TEXT NOT NULL, scanned TEXT NOT NULL,
  PRIMARY KEY(matter,document_type,path));
CREATE INDEX IF NOT EXISTS latest_writings_selected_v240
  ON latest_writings_v240(matter,document_type,selected,scanned DESC);

CREATE TABLE IF NOT EXISTS legal_research_queries_v240(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, question_hash TEXT NOT NULL,
  anonymized_query TEXT NOT NULL, redaction_report TEXT NOT NULL,
  providers TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL,
  completed TEXT NOT NULL, error_codes TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS legal_research_leads_v240(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, query_id TEXT NOT NULL,
  provider TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
  provider_url TEXT NOT NULL, official_url TEXT NOT NULL,
  identifier TEXT NOT NULL, ecli TEXT NOT NULL, court TEXT NOT NULL,
  decision_date TEXT NOT NULL, exact_excerpt TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL, verification_status TEXT NOT NULL,
  authority_id TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS legal_research_leads_query_v240
  ON legal_research_leads_v240(query_id,provider,created);

CREATE TABLE IF NOT EXISTS legal_authorities_v240(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, query_id TEXT NOT NULL,
  provider TEXT NOT NULL, provider_url TEXT NOT NULL, official_url TEXT NOT NULL,
  identifier TEXT NOT NULL, ecli TEXT NOT NULL, court TEXT NOT NULL,
  decision_date TEXT NOT NULL, title TEXT NOT NULL, exact_excerpt TEXT NOT NULL,
  official_text_sha256 TEXT NOT NULL, official_text TEXT NOT NULL,
  verification_status TEXT NOT NULL, verification_reason TEXT NOT NULL,
  retrieved TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(matter,official_url,exact_excerpt));
CREATE INDEX IF NOT EXISTS legal_authorities_matter_v240
  ON legal_authorities_v240(matter,verification_status,decision_date DESC);

CREATE TABLE IF NOT EXISTS exhibit_registry_v240(
  matter TEXT NOT NULL, path TEXT NOT NULL, filename TEXT NOT NULL,
  sha256 TEXT NOT NULL, etag TEXT NOT NULL, bytes INTEGER NOT NULL,
  duplicate_group TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  status TEXT NOT NULL, PRIMARY KEY(matter,path));
CREATE INDEX IF NOT EXISTS exhibit_registry_hash_v240
  ON exhibit_registry_v240(matter,sha256);

CREATE TABLE IF NOT EXISTS paragraph_provenance_v240(
  project_id TEXT NOT NULL, locator TEXT NOT NULL, paragraph_no INTEGER NOT NULL,
  text_hash TEXT NOT NULL, source_ids TEXT NOT NULL, source_snapshots TEXT NOT NULL,
  exact_quotes TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL,
  PRIMARY KEY(project_id,locator,paragraph_no));

CREATE TABLE IF NOT EXISTS deterministic_controls_v240(
  project_id TEXT PRIMARY KEY, matter TEXT NOT NULL, status TEXT NOT NULL,
  checks TEXT NOT NULL, blocking_reasons TEXT NOT NULL, warnings TEXT NOT NULL,
  created TEXT NOT NULL);
''',)


def ensure_schema(desk):
    for sql in SCHEMAS:desk.db.executescript(sql)
    desk.db.commit()


def _matter(desk,reference):
    value=fold(str(reference or '').strip());matches=[]
    for item in load_matters(desk.c):
        labels=[item['id'],item.get('client_name',''),PurePosixPath(item['path']).name]
        labels+=item.get('aliases',[])+item.get('references',[])
        if any(fold(str(x)).strip()==value for x in labels if str(x).strip()):matches.append(item)
    if len(matches)!=1:raise Stop('dossier_absent' if not matches else 'plusieurs_dossiers_correspondent')
    return matches[0]


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _timestamp(value):
    try:return parsedate_to_datetime(str(value)).timestamp()
    except (ValueError,TypeError,OverflowError):
        try:return datetime.fromisoformat(str(value)).timestamp()
        except (ValueError,TypeError,OverflowError):return 0


def _iso_day(value):
    try:return datetime.fromtimestamp(value,timezone.utc).date().isoformat() if value else ''
    except (ValueError,OverflowError,OSError):return ''


def _filename_date(name):
    hit=re.findall(r'(?<!\d)(20\d{2})[-_. ]?(0[1-9]|1[0-2])[-_. ]?([0-2]\d|3[01])(?!\d)',name)
    if not hit:return ''
    return max('-'.join(x) for x in hit)


def _writing_status(value):
    value=fold(value)
    if re.search(r'\b(notifie(?:e|es)?|signifie(?:e|es)?|depose(?:e|es)?|communique(?:e|es)?)\b',value):return 'notified',4
    if re.search(r'\b(signe(?:e|es)?|definitif|final)\b',value):return 'final',3
    if re.search(r'\b(projet|brouillon|draft|modele|archive|ancien)\b',value):return 'draft',1
    return 'undetermined',2


def _writing_version(value):
    hits=[int(x) for x in re.findall(r'(?:\bv(?:ersion)?\s*|\bn[°o]\s*)(\d{1,3})\b',fold(value))]
    return max(hits,default=0)


def identify_latest_writings(desk,args,dav=None,items=None):
    """Rank relevant writings and fail closed when the first rank is tied."""
    ensure_schema(desk);matter=_matter(desk,args.get('matter',''))
    dtype=str(args.get('document_type','conclusions'))
    if dtype not in WRITING_TYPES:raise Stop('type_ecritures_invalide')
    if dav is None:
        from .document_projects import _dav
        dav=_dav(desk)
    inventory=list(items if items is not None else
                   (dav.inventory_large(matter['path']) if hasattr(dav,'inventory_large') else dav.inventory(matter['path'])))
    tokens=WRITING_TYPES[dtype];candidates=[]
    for item in inventory:
        if item.get('directory') or PurePosixPath(item.get('path','')).suffix.lower() not in {'.docx','.pdf','.odt'}:continue
        name=PurePosixPath(item['path']).name;normal=fold(name)
        if tokens and not any(x in normal for x in tokens):continue
        raw=dav.download(item)
        try:text=extract(raw,name,desk.c['documents'])[:120000]
        except Stop:text=''
        status,status_rank=_writing_status(name+'\n'+text[:12000])
        day=_filename_date(name) or _iso_day(_timestamp(item.get('modified','')))
        version=_writing_version(name+'\n'+text[:4000])
        # Date is substantive; DAV modification is the deterministic final tie-breaker.
        score=(day,version,_timestamp(item.get('modified','')),status_rank,
               1 if PurePosixPath(name).suffix.lower()=='.docx' else 0)
        candidates.append({'path':clean_path(item['path']),'etag':str(item.get('etag','')),
          'modified':str(item.get('modified','')),'size':len(raw),'sha256':_sha(raw),
          'format':PurePosixPath(name).suffix.lower(),'detected_date':day,
          'detected_version':version,'writing_status':status,'score':score})
    if not candidates:raise Stop('aucune_ecriture_exploitable')
    candidates.sort(key=lambda x:(x['score'],x['path']),reverse=True)
    top=candidates[0];ambiguous=len(candidates)>1 and candidates[1]['score']==top['score']
    certainty='ambiguous' if ambiguous else 'certain'
    reason=('Deux écritures ont exactement le même rang documentaire.' if ambiguous else
      'Sélection unique par date explicite, version, modification, état et format.')
    stamp=desk.now();desk.db.execute(
      'DELETE FROM latest_writings_v240 WHERE matter=? AND document_type=?',(matter['id'],dtype))
    for row in candidates:
        desk.db.execute('''INSERT INTO latest_writings_v240
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(matter['id'],dtype,row['path'],row['etag'],
          row['modified'],row['sha256'],row['detected_date'],row['detected_version'],
          row['writing_status'],json.dumps(row['score']),int(row is top),certainty,reason,stamp))
    desk.db.commit();desk.audit('latest_writings_identified',{'matter':matter['id'],
      'document_type':dtype,'selected':top['path'],'certainty':certainty,
      'candidate_count':len(candidates)})
    return {'matter':matter['id'],'document_type':dtype,'certainty':certainty,
      'selected':top,'candidates':candidates[:20],'reason':reason,
      'requires_explicit_source_confirmation':ambiguous}


def _sensitive_values(desk,matter):
    values=[matter.get('client_name',''),*matter.get('aliases',[]),*matter.get('references',[])]
    for person in matter.get('correspondents',[]):values.append(person.get('email',''))
    try:
        rows=desk.db.execute("SELECT title,content FROM legal_memory_records WHERE matter=? AND record_type='party'",(matter['id'],))
        for row in rows:values.extend([row[0],row[1]])
    except Exception:pass
    return sorted({str(x).strip() for x in values if len(str(x).strip())>=3},key=len,reverse=True)


def anonymize_query(desk,matter,question):
    question=re.sub(r'[\x00-\x1f]',' ',str(question or '')).strip()
    if not question or len(question)>3000:raise Stop('question_juridique_invalide')
    out=question;report=[]
    for pos,value in enumerate(_sensitive_values(desk,matter),1):
        pattern=re.compile(re.escape(value),re.I)
        if pattern.search(out):
            kind='email' if '@' in value else ('reference' if any(c.isdigit() for c in value) else 'identite')
            out=pattern.sub('[DONNEE_'+kind.upper()+'_'+str(pos)+']',out)
            report.append({'type':kind,'value_hash':digest(value),'occurrences':1})
    patterns=(('email',r'(?i)\b[^\s@]+@[^\s@]+\.[a-z]{2,}\b'),
      ('iban',r'(?i)\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){11,30}\b'),
      ('telephone',r'(?<!\d)(?:\+33|0)[1-9](?:[ .-]?\d{2}){4}(?!\d)'),
      ('rg',r'(?i)\b(?:RG|Portalis)\s*[:n°o-]*\s*[A-Z0-9./-]{4,}\b'))
    for kind,pattern in patterns:
        hits=re.findall(pattern,out)
        if hits:
            out=re.sub(pattern,'[DONNEE_'+kind.upper()+']',out)
            report.append({'type':kind,'value_hash':'regex','occurrences':len(hits)})
    folded=fold(out)
    residue=[digest(x) for x in _sensitive_values(desk,matter)
      if len(x)>=4 and fold(x) in folded]
    if residue:raise Stop('anonymisation_juridique_incomplete')
    return out,report


def prepare_research_query(desk,args):
    """Prepare the only string that may be sent to an external legal MCP."""
    ensure_schema(desk);matter=_matter(desk,args.get('matter',''))
    anonymous,redactions=anonymize_query(desk,matter,args.get('question',''))
    requested=args.get('providers') or list(PROVIDERS)
    if isinstance(requested,str):requested=[x.strip().lower() for x in requested.split(',') if x.strip()]
    if not isinstance(requested,list) or not requested or any(x not in PROVIDERS for x in requested):
        raise Stop('fournisseur_juridique_invalide')
    qid=digest(matter['id']+'|'+anonymous+'|'+','.join(requested))
    stamp=desk.now();desk.db.execute('''INSERT OR REPLACE INTO legal_research_queries_v240
      VALUES(?,?,?,?,?,?,?,?,?,?)''',(qid,matter['id'],digest(str(args.get('question',''))),anonymous,
      json.dumps(redactions,ensure_ascii=False),json.dumps(requested),'prepared',stamp,'','[]'))
    desk.db.commit();desk.audit('legal_mcp_query_prepared',{'matter':matter['id'],'query':qid,
      'providers':requested,'redactions':len(redactions)})
    return {'query_id':qid,'matter':matter['id'],'anonymized_query':anonymous,
      'redaction_report':redactions,'providers':requested,'status':'prepared',
      'mcp_instruction':'Transmettre exclusivement anonymized_query au fournisseur choisi. Ne joindre aucun courriel, nom, chemin ou document du dossier.'}


class _HTMLText(HTMLParser):
    def __init__(self):super().__init__();self.parts=[]
    def handle_data(self,data):
        if data.strip():self.parts.append(data.strip())


def _text(raw,content_type=''):
    if not isinstance(raw,(bytes,bytearray)) or not raw:raise Stop('texte_officiel_absent')
    if len(raw)>6_000_000:raise Stop('decision_officielle_trop_volumineuse')
    try:value=bytes(raw).decode('utf-8')
    except UnicodeDecodeError:
        try:value=bytes(raw).decode('latin-1')
        except UnicodeDecodeError:raise Stop('decision_officielle_illisible') from None
    if '<html' in value[:2000].lower() or 'text/html' in content_type:
        parser=_HTMLText();parser.feed(value);value='\n'.join(parser.parts)
    value=re.sub(r'\s+',' ',value).strip()
    if len(value)<80:raise Stop('texte_officiel_insuffisant')
    return value


def _host_allowed(host,configured=()):
    allowed=set(OFFICIAL_HOSTS)|{str(x).lower() for x in configured}
    return any(host==x or host.endswith('.'+x) for x in allowed)


def _fetch_official(url,configured_hosts=()):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.query and len(parsed.query)>2000:
        raise Stop('url_officielle_invalide')
    if not _host_allowed((parsed.hostname or '').lower(),configured_hosts):raise Stop('source_non_officielle')
    base=parsed.scheme+'://'+parsed.netloc
    return HTTP(base,timeout=60).request('GET',url,headers={'Accept':'text/html,application/json,text/plain'},limit=6_000_000)


LEGAL_SOURCE_REQUIRED=('authority_id','reference','official_url','exact_excerpt','official_text_sha256')


def legal_source(result):
    """5.6.24 (F07) : objet commun de source juridique partagé par la recherche, le rédacteur et le contrôleur — identité officielle
    (authority_id), type, référence (identifiant ou ECLI), URL officielle, extrait exact, empreinte du texte officiel, date de vérification.
    Retourne None, jamais un objet partiel, si un champ obligatoire manque ou si la source n'est pas citable."""
    if not isinstance(result,dict):return None
    if not (result.get('citable') or result.get('status')=='verified'):return None
    src={'authority_id':str(result.get('authority_id') or ''),'kind':str(result.get('kind') or 'decision'),
      'reference':str(result.get('identifier') or result.get('ecli') or result.get('reference') or ''),'official_url':str(result.get('official_url') or ''),
      'exact_excerpt':str(result.get('exact_excerpt') or ''),'official_text_sha256':str(result.get('official_text_sha256') or ''),
      'verified_at':str(result.get('verified_at') or ''),'court':str(result.get('court') or ''),'date':str(result.get('date') or ''),'title':str(result.get('title') or '')}
    if any(not src[k] for k in LEGAL_SOURCE_REQUIRED):return None
    return src


def verify_official_decision(desk,args,fetcher=None):
    ensure_schema(desk);matter=_matter(desk,args.get('matter',''))
    url=str(args.get('official_url','')).strip();identifier=str(args.get('identifier','')).strip()[:300]
    ecli=str(args.get('ecli','')).strip()[:300];court=str(args.get('court','')).strip()[:300]
    decision_date=str(args.get('date','')).strip()[:40];title=str(args.get('title','')).strip()[:500]
    quote=re.sub(r'\s+',' ',str(args.get('exact_excerpt','')).strip())
    if len(quote)<30 or len(quote)>5000:raise Stop('extrait_exact_requis')
    configured=desk.c.get('legal_research',{}).get('official_hosts',[])
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not _host_allowed((parsed.hostname or '').lower(),configured):raise Stop('source_non_officielle')
    raw=(fetcher(url) if fetcher else _fetch_official(url,configured))
    if bytes(raw).startswith(b'%PDF'):
        try:official=re.sub(r'\s+',' ',extract(bytes(raw),'decision-officielle.pdf',desk.c['documents'])).strip()
        except Stop:raise Stop('decision_officielle_illisible') from None
    else:official=_text(raw)
    if len(official)<80:raise Stop('texte_officiel_insuffisant')
    normalized=unicodedata.normalize('NFKC',re.sub(r'\s+',' ',official)).strip()
    quote_norm=unicodedata.normalize('NFKC',quote).strip()
    quote_ok=quote_norm in normalized
    identity_space=re.sub(r'[^a-z0-9]','',fold(normalized+' '+url))
    anchors=[re.sub(r'[^a-z0-9]','',fold(x)) for x in (identifier,ecli) if x]
    identity_ok=bool(anchors) and all(x in identity_space for x in anchors)
    verified=quote_ok and identity_ok
    reason=('verified_official_text_and_exact_excerpt' if verified else
      ('exact_excerpt_not_found' if not quote_ok else 'decision_identifier_not_found'))
    authority_id=digest('|'.join([matter['id'],url,identifier,ecli,quote]))
    provider=str(args.get('provider','manual'))[:40];provider_url=str(args.get('provider_url',''))[:2000]
    stamp=desk.now();status='verified' if verified else 'rejected'
    desk.db.execute('''INSERT OR REPLACE INTO legal_authorities_v240
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(authority_id,matter['id'],str(args.get('query_id',''))[:64],
      provider,provider_url,url,identifier,ecli,court,decision_date,title,quote,_sha(official.encode()),
      official[:2_000_000],status,reason,stamp,stamp))
    desk.db.commit();desk.audit('official_decision_verified',{'matter':matter['id'],
      'authority':authority_id,'status':status,'official_url':url,'text_sha256':_sha(official.encode())})
    return {'authority_id':authority_id,'status':status,'official_url':url,
      'identifier':identifier,'ecli':ecli,'court':court,'date':decision_date,'title':title,'kind':'decision',
      'exact_excerpt':quote if verified else '', 'official_text_sha256':_sha(official.encode()),
      'verification_reason':reason,'citable':verified,'verified_at':stamp}


def _provider_results(desk,name,query,limit):
    cfg=desk.c.get('legal_research',{}).get('providers',{}).get(name,{})
    if not cfg.get('enabled'):return []
    url=str(cfg.get('url','')).rstrip('/');path=str(cfg.get('search_path','/search'))
    if not url:return []
    client=HTTP(url,timeout=min(120,int(cfg.get('timeout_seconds',45))))
    token_file=str(cfg.get('api_key_file',''))
    if token_file:client.headers['Authorization']='Bearer '+read_secret(token_file)
    result=client.json('POST',path,{'query':query,'limit':limit})
    rows=result.get('results',result.get('items',[])) if isinstance(result,dict) else []
    if not isinstance(rows,list):raise Stop('reponse_fournisseur_juridique_invalide')
    clean=[]
    for item in rows[:limit]:
        if not isinstance(item,dict):continue
        clean.append({'provider':name,'title':str(item.get('title',''))[:500],
          'summary':str(item.get('summary') or item.get('excerpt') or '')[:5000],
          'provider_url':str(item.get('url',''))[:2000],
          'official_url':str(item.get('official_url',''))[:2000],
          'identifier':str(item.get('identifier') or item.get('decision_id') or '')[:300],
          'ecli':str(item.get('ecli',''))[:300],'court':str(item.get('court',''))[:300],
          'date':str(item.get('date',''))[:40],
          'exact_excerpt':str(item.get('exact_excerpt',''))[:5000]})
    return clean


def _clean_leads(provider,rows,limit):
    if not isinstance(rows,list):raise Stop('resultats_mcp_juridiques_invalides')
    clean=[]
    for item in rows[:limit]:
        if not isinstance(item,dict):continue
        clean.append({'provider':provider,'title':str(item.get('title',''))[:500],
          'summary':str(item.get('summary') or item.get('excerpt') or '')[:5000],
          'provider_url':str(item.get('provider_url') or item.get('url') or '')[:2000],
          'official_url':str(item.get('official_url',''))[:2000],
          'identifier':str(item.get('identifier') or item.get('decision_id') or '')[:300],
          'ecli':str(item.get('ecli',''))[:300],'court':str(item.get('court',''))[:300],
          'date':str(item.get('date',''))[:40],
          'exact_excerpt':str(item.get('exact_excerpt',''))[:5000]})
    return clean


def _save_lead(desk,matter,qid,lead,authority_id='',status='unverified'):
    canonical=json.dumps(lead,ensure_ascii=False,sort_keys=True)
    lead_id=digest('|'.join([matter,qid,lead.get('provider',''),canonical]))
    desk.db.execute('''INSERT OR REPLACE INTO legal_research_leads_v240
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(lead_id,matter,qid,lead.get('provider',''),
      lead.get('title',''),lead.get('summary',''),lead.get('provider_url',''),
      lead.get('official_url',''),lead.get('identifier',''),lead.get('ecli',''),
      lead.get('court',''),lead.get('date',''),lead.get('exact_excerpt',''),
      digest(canonical),status,authority_id,desk.now()))
    return lead_id


def import_mcp_results(desk,args,official_fetcher=None):
    """Import MCP discovery output after a locally prepared anonymised query.

    MCP providers are never trusted as official sources.  Each eligible hit is
    independently downloaded from an allow-listed official host and matched.
    """
    ensure_schema(desk);qid=str(args.get('query_id','')).strip()
    if not re.fullmatch(r'[a-f0-9]{64}',qid):raise Stop('requete_juridique_invalide')
    query=desk.db.execute('SELECT * FROM legal_research_queries_v240 WHERE id=?',(qid,)).fetchone()
    if not query:raise Stop('requete_juridique_absente')
    provider=str(args.get('provider','')).strip().lower()
    allowed=json.loads(query['providers'])
    if provider not in PROVIDERS or provider not in allowed:raise Stop('fournisseur_juridique_non_autorise')
    supplied=str(args.get('anonymized_query','')).strip()
    if supplied and supplied!=query['anonymized_query']:raise Stop('requete_mcp_non_conforme')
    raw=args.get('results',[])
    if isinstance(raw,str):
        try:raw=json.loads(raw)
        except ValueError:raise Stop('resultats_mcp_juridiques_invalides') from None
    if isinstance(raw,dict):raw=raw.get('results',raw.get('items',[]))
    limit=max(1,min(int(args.get('limit',20)),20));leads=_clean_leads(provider,raw,limit)
    verified=[];errors=[]
    for lead in leads:
        authority_id='';status='unverified'
        if lead.get('official_url') and lead.get('exact_excerpt') and (lead.get('identifier') or lead.get('ecli')):
            try:
                result=verify_official_decision(desk,{**lead,'matter':query['matter'],'query_id':qid},fetcher=official_fetcher)
                authority_id=result['authority_id'];status=result['status']
                if result['citable']:verified.append(result)
            except Stop as ex:
                status='rejected';errors.append({'lead':lead.get('identifier') or lead.get('title'),'error':str(ex)})
        _save_lead(desk,query['matter'],qid,lead,authority_id,status)
    final='completed' if not errors else ('partial' if leads else 'error')
    desk.db.execute('UPDATE legal_research_queries_v240 SET status=?,completed=?,error_codes=? WHERE id=?',
      (final,desk.now(),json.dumps(errors,ensure_ascii=False),qid));desk.db.commit()
    desk.audit('legal_mcp_results_imported',{'matter':query['matter'],'query':qid,
      'provider':provider,'leads':len(leads),'verified':len(verified),'errors':len(errors)})
    return {'query_id':qid,'matter':query['matter'],'provider':provider,'leads':leads,
      'verified_authorities':verified,'sources_juridiques':[s for s in (legal_source(v) for v in verified) if s],'status':final,'errors':errors,
      'warning':'Les résultats MCP sont des pistes. Seules les décisions présentes dans verified_authorities sont citables.'}


def research(desk,args,provider_runner=None,official_fetcher=None):
    """Query configured discovery providers and verify eligible hits officially."""
    prepared=prepare_research_query(desk,args);matter=_matter(desk,prepared['matter'])
    anonymous=prepared['anonymized_query'];redactions=prepared['redaction_report']
    requested=prepared['providers'];qid=prepared['query_id']
    limit=max(1,min(int(args.get('limit',8)),20))
    desk.db.execute('UPDATE legal_research_queries_v240 SET status=? WHERE id=?',('running',qid));desk.db.commit()
    leads=[];errors=[]
    for name in requested:
        if provider_runner is None:
            settings=desk.c.get('legal_research',{}).get('providers',{}).get(name,{})
            if not settings.get('enabled') or not settings.get('url'):
                errors.append({'provider':name,'error':'connecteur_juridique_non_configure'})
                continue
        try:
            rows=(provider_runner(name,anonymous,limit) if provider_runner else _provider_results(desk,name,anonymous,limit)) or []
            leads.extend(_clean_leads(name,rows,limit))
        except Stop as ex:errors.append({'provider':name,'error':str(ex)})
    verified=[]
    for lead in leads[:limit*len(requested)]:
        authority_id='';lead_status='unverified'
        if lead.get('official_url') and lead.get('exact_excerpt') and (lead.get('identifier') or lead.get('ecli')):
            try:
                result=verify_official_decision(desk,{**lead,'matter':matter['id'],'query_id':qid},fetcher=official_fetcher)
                authority_id=result['authority_id'];lead_status=result['status']
                if result['citable']:verified.append(result)
            except Stop as ex:
                lead_status='rejected';errors.append({'provider':lead.get('provider',''),'error':str(ex),
                  'identifier':lead.get('identifier','')})
        _save_lead(desk,matter['id'],qid,lead,authority_id,lead_status)
    status='completed' if not errors else ('partial' if leads else 'error')
    desk.db.execute('UPDATE legal_research_queries_v240 SET status=?,completed=?,error_codes=? WHERE id=?',
      (status,desk.now(),json.dumps(errors,ensure_ascii=False),qid));desk.db.commit()
    desk.audit('anonymized_legal_research',{'matter':matter['id'],'query':qid,
      'providers':requested,'redactions':len(redactions),'leads':len(leads),'verified':len(verified)})
    return {'query_id':qid,'matter':matter['id'],'anonymized_query':anonymous,
      'redaction_report':redactions,'providers':requested,'leads':leads,'verified_authorities':verified,
      'status':status,'errors':errors,
      'warning':'Les résultats fournisseurs sont des pistes. Seules les décisions figurant dans verified_authorities sont citables.'}


def research_enabled_mcp(desk,matter,question,limit=8,official_fetcher=None):
    """Use enabled Lawve MCP connectors and independently verify official texts."""
    target=_matter(desk,matter)
    anonymous,redactions=anonymize_query(desk,target,question)
    from .extensions364 import legal_connector_search
    connector_results=legal_connector_search(desk,anonymous,limit)
    verified=[];leads=[];reports=[]
    for result in connector_results:
        provider=result.get('provider','')
        if result.get('status')!='ok' or provider not in PROVIDERS:
            reports.append({k:result.get(k,'') for k in ('connector','provider','status','error')});continue
        prepared=prepare_research_query(desk,{'matter':target['id'],'question':question,
                                             'providers':[provider]})
        imported=import_mcp_results(desk,{'query_id':prepared['query_id'],'provider':provider,
          'anonymized_query':prepared['anonymized_query'],'results':result.get('rows',[]),'limit':limit},
          official_fetcher=official_fetcher)
        leads.extend(imported['leads']);verified.extend(imported['verified_authorities'])
        reports.append({'connector':result.get('connector',''),'provider':provider,
          'status':imported['status'],'leads':len(imported['leads']),
          'verified':len(imported['verified_authorities'])})
    desk.audit('legal_mcp_extensions_researched',{'matter':target['id'],
      'connectors':len(connector_results),'verified':len(verified),'redactions':len(redactions)})
    return {'matter':target['id'],'anonymized_query':anonymous,'redaction_report':redactions,
      'connectors':reports,'leads':leads,'verified_authorities':verified,
      'status':'completed' if connector_results and all(x.get('status') in ('ok','completed') for x in reports)
        else ('partial' if connector_results else 'not_configured'),
      'warning':'Seules les décisions revérifiées sur une source officielle sont citables.'}


def authorities(desk,matter,status='all',limit=100):
    ensure_schema(desk);m=_matter(desk,matter);limit=max(1,min(int(limit),300))
    where='matter=?';params=[m['id']]
    if status!='all':
        if status not in ('verified','rejected'):raise Stop('etat_jurisprudence_invalide')
        where+=' AND verification_status=?';params.append(status)
    params.append(limit);rows=[]
    for row in desk.db.execute('SELECT * FROM legal_authorities_v240 WHERE '+where+' ORDER BY decision_date DESC,created DESC LIMIT ?',params):
        item=dict(row);item.pop('official_text',None);item['citable']=item['verification_status']=='verified';rows.append(item)
    return {'matter':m['id'],'authorities':rows}


def register_exhibits(desk,args,dav=None):
    """Hash actual Nextcloud bytes; equal fingerprints are exact duplicates."""
    ensure_schema(desk);matter=_matter(desk,args.get('matter',''))
    if dav is None:
        from .document_projects import _dav
        dav=_dav(desk)
    limit=max(1,min(int(args.get('limit',200)),500));inventory=(
      dav.inventory_large(matter['path']) if hasattr(dav,'inventory_large') else dav.inventory(matter['path']))
    candidates=[x for x in inventory if not x.get('directory') and
      (re.search(r'(?i)\b(piece|pi[eè]ce|annexe|justificatif)\b',PurePosixPath(x['path']).name)
       or PurePosixPath(x['path']).suffix.lower() in {'.pdf','.jpg','.jpeg','.png','.docx','.odt'})][:limit]
    stamp=desk.now();seen=[];errors=[]
    desk.db.execute("UPDATE exhibit_registry_v240 SET status='missing' WHERE matter=?",(matter['id'],))
    for item in candidates:
        try:raw=dav.download(item)
        except Stop as ex:
            path=clean_path(item['path']);errors.append({'path':path,'error':str(ex)})
            desk.db.execute("UPDATE exhibit_registry_v240 SET status='unreadable',last_seen=? WHERE matter=? AND path=?",
              (stamp,matter['id'],path));continue
        sha=_sha(raw);path=clean_path(item['path']);old=desk.db.execute(
          'SELECT first_seen FROM exhibit_registry_v240 WHERE matter=? AND path=?',(matter['id'],path)).fetchone()
        desk.db.execute('''INSERT OR REPLACE INTO exhibit_registry_v240
          VALUES(?,?,?,?,?,?,?,?,?,?)''',(matter['id'],path,PurePosixPath(path).name,sha,
          str(item.get('etag','')),len(raw),sha,old['first_seen'] if old else stamp,stamp,'present'))
        seen.append({'path':path,'filename':PurePosixPath(path).name,'sha256':sha,'bytes':len(raw)})
    desk.db.commit();groups={}
    for item in seen:groups.setdefault(item['sha256'],[]).append(item['path'])
    duplicates=[{'sha256':sha,'paths':paths} for sha,paths in groups.items() if len(paths)>1]
    desk.audit('exhibit_registry_refreshed',{'matter':matter['id'],'hashed':len(seen),
      'duplicate_groups':len(duplicates),'errors':len(errors)})
    return {'matter':matter['id'],'hashed':len(seen),'exhibits':seen,
      'exact_duplicates':duplicates,'errors':errors,
      'warning':'Un doublon est affirmé uniquement lorsque les empreintes SHA-256 des octets sont identiques.'}


def exhibits(desk,matter,limit=300):
    ensure_schema(desk);m=_matter(desk,matter);limit=max(1,min(int(limit),500))
    rows=[dict(x) for x in desk.db.execute('''SELECT matter,path,filename,sha256,etag,bytes,
      duplicate_group,first_seen,last_seen,status FROM exhibit_registry_v240 WHERE matter=?
      ORDER BY filename,path LIMIT ?''',(m['id'],limit))]
    groups={}
    for row in rows:
        if row['status']=='present':groups.setdefault(row['sha256'],[]).append(row['path'])
    return {'matter':m['id'],'exhibits':rows,
      'exact_duplicates':[{'sha256':x,'paths':p} for x,p in groups.items() if len(p)>1]}


def record_paragraph_provenance(desk,project_id,draft,packet):
    ensure_schema(desk);by_id={x['id']:x for x in packet};stamp=desk.now();rows=[]
    entries=[('introduction',draft.get('introduction',''),draft.get('introduction_source_ids',[]))]
    entries += [('section:'+str(i),x.get('body',''),x.get('source_ids',[])) for i,x in enumerate(draft.get('sections',[]),1)]
    entries += [('request:'+str(i),x.get('text',''),x.get('source_ids',[])) for i,x in enumerate(draft.get('requests',[]),1)]
    desk.db.execute('DELETE FROM paragraph_provenance_v240 WHERE project_id=?',(project_id,))
    for locator,text,source_ids in entries:
        paragraphs=[x.strip() for x in re.split(r'\n\s*\n|\n',text) if x.strip()] or ['']
        for no,paragraph in enumerate(paragraphs,1):
            snapshots=[];quotes=[]
            for sid in source_ids:
                source=by_id.get(sid,{})
                excerpt=str(source.get('exact_excerpt') or source.get('excerpt') or '')
                snapshots.append({'source_id':sid,'path':source.get('path',''),
                  'etag':source.get('etag',''),'excerpt_sha256':_sha(excerpt.encode())})
                if excerpt:quotes.append({'source_id':sid,'quote':excerpt[:1500],
                  'quote_sha256':_sha(excerpt[:1500].encode())})
            status='complete' if source_ids and len(snapshots)==len(source_ids) and len(quotes)==len(source_ids) and all(x['quote'] for x in quotes) else 'incomplete'
            desk.db.execute('''INSERT INTO paragraph_provenance_v240 VALUES(?,?,?,?,?,?,?,?,?)''',
              (project_id,locator,no,_sha(paragraph.encode()),json.dumps(source_ids,ensure_ascii=False),
               json.dumps(snapshots,ensure_ascii=False),json.dumps(quotes,ensure_ascii=False),status,stamp))
            rows.append({'locator':locator,'paragraph_no':no,'text_hash':_sha(paragraph.encode()),
              'source_ids':source_ids,'source_snapshots':snapshots,'exact_quotes':quotes,'status':status})
    desk.db.commit();return rows


def provenance(desk,project_id):
    ensure_schema(desk)
    if not re.fullmatch(r'[a-f0-9]{32}',str(project_id)):raise Stop('projet_document_invalide')
    rows=[]
    for row in desk.db.execute('SELECT * FROM paragraph_provenance_v240 WHERE project_id=? ORDER BY locator,paragraph_no',(project_id,)):
        item=dict(row)
        for key in ('source_ids','source_snapshots','exact_quotes'):item[key]=json.loads(item[key])
        rows.append(item)
    if not rows:raise Stop('provenance_projet_absente')
    return {'project_id':project_id,'paragraphs':rows,
      'complete':all(x['status']=='complete' for x in rows)}


def _tokens(text):
    return set(re.findall(r'(?i)\b(?:\d{1,3}(?:[ .]\d{3})*(?:[,.]\d+)?\s*(?:€|euros?)|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|RG\s*[A-Z0-9./-]+|ECLI:[A-Z0-9:.-]+)\b',text))


def deterministic_control(desk,data,packet,project_id):
    """Checks that do not rely on a language model or probabilistic judgement."""
    ensure_schema(desk);draft=data['draft'];by_id={x['id']:x for x in packet}
    provenance_rows=record_paragraph_provenance(desk,project_id,draft,packet)
    used=[]
    for key in ('source_ids','introduction_source_ids'):used+=draft.get(key,[])
    for item in draft.get('sections',[])+draft.get('requests',[])+draft.get('exhibits_referenced',[]):used+=item.get('source_ids',[])
    legal={x['id']:x for x in data.get('legal_research',[])}
    unsupported=[]
    for locator,text,ids in ([('introduction',draft.get('introduction',''),draft.get('introduction_source_ids',[]))]+
      [('section:'+str(i),x.get('body',''),x.get('source_ids',[])) for i,x in enumerate(draft.get('sections',[]),1)]+
      [('request:'+str(i),x.get('text',''),x.get('source_ids',[])) for i,x in enumerate(draft.get('requests',[]),1)]):
        source_text=' '.join(str(by_id.get(x,{}).get('excerpt','')) for x in ids)
        missing=[x for x in _tokens(text) if fold(x) not in fold(source_text)]
        if missing:unsupported.append({'locator':locator,'tokens':missing})
    labels=[fold(x.get('label','')) for x in draft.get('exhibits_referenced',[]) if x.get('label')]
    forbidden=re.compile(r'(?i)\b(a été (?:envoyé|déposé|signé)|avons (?:envoyé|déposé|payé)|RPVA effectué)\b')
    text='\n'.join([draft.get('introduction','')]+[x.get('body','') for x in draft.get('sections',[])]+[x.get('text','') for x in draft.get('requests',[])])
    checks={
      'all_source_ids_known':all(x in by_id for x in used),
      'paragraph_provenance_complete':all(x['status']=='complete' for x in provenance_rows),
      'official_text_retrieved_for_every_legal_citation':all(not x.startswith('legal-') or
        (legal.get(x,{}).get('officially_verified') and legal.get(x,{}).get('official_text_sha256') and legal.get(x,{}).get('exact_excerpt')) for x in used),
      'numeric_and_date_tokens_supported':not unsupported,
      'no_duplicate_exhibit_labels':len(labels)==len(set(labels)),
      'all_exhibits_have_sources':all(bool(x.get('source_ids')) for x in draft.get('exhibits_referenced',[])),
      'future_paths_unique':len(data.get('future_files',[]))==len(set(data.get('future_files',[]))),
      'source_snapshot_present':bool(data.get('source',{}).get('sha256')),
      'latest_writings_certain':data.get('latest_writings',{}).get('certainty','certain') in ('certain','explicit'),
      'destination_inside_matter':str(data.get('destination_folder','')).startswith(str(data.get('matter',{}).get('path','')).rstrip('/')+'/'),
      'no_claim_of_external_execution':not bool(forbidden.search(text)),
      'external_query_anonymization_recorded':bool(data.get('safety',{}).get('external_queries_anonymized')),
    }
    blocking=[key for key,value in checks.items() if not value]
    warnings=[]
    if unsupported:warnings.append('Nombres, dates ou identifiants non retrouvés dans les sources : '+json.dumps(unsupported,ensure_ascii=False))
    status='blocked' if blocking else 'passed'
    desk.db.execute('INSERT OR REPLACE INTO deterministic_controls_v240 VALUES(?,?,?,?,?,?,?)',
      (project_id,data['matter']['id'],status,json.dumps(checks,ensure_ascii=False),
       json.dumps(blocking,ensure_ascii=False),json.dumps(warnings,ensure_ascii=False),desk.now()))
    desk.db.commit();return {'status':status,'checks':checks,'blocking_reasons':blocking,
      'warnings':warnings,'paragraph_provenance':provenance_rows}


def deterministic_control_report(desk,project_id):
    ensure_schema(desk)
    if not re.fullmatch(r'[a-f0-9]{32}',str(project_id)):raise Stop('projet_document_invalide')
    row=desk.db.execute('SELECT * FROM deterministic_controls_v240 WHERE project_id=?',(project_id,)).fetchone()
    if not row:raise Stop('controle_deterministe_absent')
    item=dict(row)
    for key in ('checks','blocking_reasons','warnings'):item[key]=json.loads(item[key])
    return item


def perform(desk,kind,args):
    if kind=='legal_research':return research(desk,args)
    if kind=='import_mcp_legal_results':return import_mcp_results(desk,args)
    if kind=='verify_official_decision':return verify_official_decision(desk,args)
    if kind=='identify_latest_writings':return identify_latest_writings(desk,args)
    if kind=='refresh_exhibit_registry':return register_exhibits(desk,args)
    raise Stop('action_inconnue')

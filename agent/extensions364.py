"""Lawve.ai extension registry retained by AxiorHub 4.2.0.

The registry deliberately separates catalogue metadata, credentials and executable
content.  Remote MCP endpoints can be registered and tested, while skill/plugin
archives are inspected and quarantined: only declarative text is made available to
the model after an explicit lawyer approval.  No downloaded script is executed.
"""
from datetime import datetime, timezone
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import sqlite3
import urllib.parse
import zipfile

from .common import HTTP, Stop, private_json, read_secret


KINDS = {'connector', 'skill', 'plugin'}
AUTH_TYPES = {'none', 'bearer', 'oauth2'}
SAFE_TEXT_SUFFIXES = {'.md', '.txt', '.json', '.yaml', '.yml', '.toml'}
EXECUTABLE_SUFFIXES = {'.py', '.pyc', '.js', '.mjs', '.cjs', '.sh', '.bash',
                       '.php', '.phar', '.pl', '.rb', '.exe', '.dll', '.so',
                       '.dylib', '.jar', '.class', '.wasm', '.bin', '.ps1'}
LAWVE_PATTERN = re.compile(
    r'^/@(?P<author>[a-z0-9][a-z0-9_-]{0,79})/(?P<kind>connector|skill|plugin)/'
    r'(?P<slug>[a-z0-9][a-z0-9_-]{0,99})/?$')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _identifier(value):
    value = str(value or '').strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', value):
        raise Stop('identifiant_extension_invalide')
    return value


def parse_lawve_url(value):
    """Accept only canonical public catalogue item URLs, never arbitrary downloads."""
    raw = str(value or '').strip()
    parts = urllib.parse.urlsplit(raw)
    if (parts.scheme != 'https' or (parts.hostname or '').lower() != 'lawve.ai' or
            parts.username or parts.password or parts.port not in (None, 443) or
            parts.query or parts.fragment):
        raise Stop('url_lawve_invalide')
    match = LAWVE_PATTERN.fullmatch(parts.path)
    if not match:
        raise Stop('fiche_lawve_invalide')
    data = match.groupdict()
    data['url'] = 'https://lawve.ai/@{author}/{kind}/{slug}'.format(**data)
    data['id'] = data['kind'] + '-' + data['author'] + '-' + data['slug']
    return data


def validate_endpoint(value, resolve=False):
    value = str(value or '').strip().rstrip('/')
    parts = urllib.parse.urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or
            parts.port not in (None, 443) or parts.query or parts.fragment):
        raise Stop('url_mcp_https_invalide')
    host = parts.hostname.lower()
    if host in ('localhost', 'localhost.localdomain') or host.endswith('.local'):
        raise Stop('hote_mcp_prive_refuse')
    try:
        literal = ipaddress.ip_address(host)
        if not literal.is_global:
            raise Stop('hote_mcp_prive_refuse')
    except ValueError:
        pass
    if resolve:
        try:
            addresses = {row[4][0] for row in socket.getaddrinfo(host, 443,
                         type=socket.SOCK_STREAM)}
        except socket.gaierror:
            raise Stop('hote_mcp_introuvable') from None
        if not addresses:
            raise Stop('hote_mcp_introuvable')
        for address in addresses:
            try:
                if not ipaddress.ip_address(address).is_global:
                    raise Stop('hote_mcp_prive_refuse')
            except ValueError:
                raise Stop('hote_mcp_invalide') from None
    if len(value) > 1000:
        raise Stop('url_mcp_trop_longue')
    return value


def _vault_dir(desk):
    path = Path(desk.c['state_dir']) / 'vault' / 'extensions'
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _archive_dir(desk):
    path = Path(desk.c['state_dir']) / 'extension-quarantine'
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _save_secret(desk, extension_id, value):
    value = str(value or '').strip()
    if not 8 <= len(value) <= 4096 or any(c in value for c in ('\n', '\r', '\x00')):
        raise Stop('secret_extension_invalide')
    target = _vault_dir(desk) / (_identifier(extension_id) + '.secret')
    temporary = target.with_name(target.name + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(value + '\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temporary, target);os.chmod(target, 0o600)
    finally:
        if temporary.exists():temporary.unlink()
    return str(target)


def _public(item):
    result = dict(item)
    secret = result.pop('secret_file', '')
    result['secret_configured'] = bool(secret and Path(secret).is_file())
    result.pop('skill_text', None)
    archive = result.pop('archive_path', '')
    result['archive_present'] = bool(archive and Path(archive).is_file())
    return result


def list_items(desk):
    rows = desk.db.execute(
        "SELECT key,value FROM settings WHERE key LIKE 'lawve:item:%' ORDER BY key").fetchall()
    result = []
    for row in rows:
        try:item = json.loads(row['value'])
        except (ValueError, TypeError):continue
        if isinstance(item, dict):result.append(_public(item))
    return result


def register_item(desk, source_url, name='', endpoint='', auth_type='none', token='',
                  allow_external=False, purposes=None, license_name=''):
    source = parse_lawve_url(source_url)
    extension_id = _identifier(source['id'])
    auth_type = str(auth_type or 'none').strip().lower()
    if auth_type not in AUTH_TYPES:raise Stop('authentification_extension_invalide')
    if source['kind'] == 'connector':
        endpoint = validate_endpoint(endpoint)
    elif endpoint:
        raise Stop('endpoint_reserve_aux_connecteurs')
    previous = desk.settings('lawve:item:' + extension_id, {}) or {}
    secret_file = str(previous.get('secret_file') or '')
    if token:secret_file = _save_secret(desk, extension_id, token)
    if auth_type == 'bearer' and not (secret_file and Path(secret_file).is_file()):
        raise Stop('secret_extension_absent')
    if auth_type == 'none':secret_file = ''
    from .ai_gateway import PURPOSES
    selected = []
    for purpose in purposes or []:
        if purpose not in PURPOSES:raise Stop('fonction_ia_invalide')
        if purpose not in selected:selected.append(purpose)
    item = {
        'id': extension_id, 'kind': source['kind'], 'author': source['author'],
        'slug': source['slug'], 'name': str(name or source['slug']).strip()[:160],
        'source_url': source['url'], 'license': str(license_name or '').strip()[:80],
        'endpoint': endpoint, 'transport': 'streamable_http' if endpoint else '',
        'auth_type': auth_type, 'secret_file': secret_file,
        'external_data_allowed': bool(allow_external), 'purposes': selected,
        'enabled': False, 'status': ('configured' if source['kind']=='connector' else 'archive_required'),
        'created_at': previous.get('created_at') or _now(), 'updated_at': _now(),
        'last_test': previous.get('last_test', {}),
        'archive_path': previous.get('archive_path', ''),
        'archive_sha256': previous.get('archive_sha256', ''),
        'archive_inventory': previous.get('archive_inventory', []),
        'skill_text': previous.get('skill_text', ''),
        'security_review': previous.get('security_review', {}),
    }
    desk.setting('lawve:item:' + extension_id, item)
    desk.c.setdefault('lawve_extensions', {})[extension_id] = item
    desk.audit('extension_lawve_enregistree', {
        'id': extension_id, 'kind': item['kind'], 'auth_type': auth_type,
        'external_data_allowed': item['external_data_allowed'], 'purposes': selected,
        'secret_replaced': bool(token)})
    return _public(item)


def import_archive(desk, extension_id, raw, filename):
    extension_id = _identifier(extension_id)
    item = desk.settings('lawve:item:' + extension_id)
    if not item or item.get('kind') not in ('skill', 'plugin'):
        raise Stop('archive_extension_non_attendue')
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 12_000_000:
        raise Stop('taille_archive_extension_invalide')
    name = PurePosixPath(str(filename or '').replace('\\', '/')).name
    if not name.lower().endswith('.zip'):
        raise Stop('format_archive_extension_invalide')
    inventory=[];texts={};total=0;blocked=[]
    try:
        archive=zipfile.ZipFile(io.BytesIO(raw))
        infos=archive.infolist()
        if not 1 <= len(infos) <= 200:raise Stop('nombre_fichiers_extension_invalide')
        for info in infos:
            path=PurePosixPath(info.filename)
            if (path.is_absolute() or '..' in path.parts or '\x00' in info.filename or
                    info.flag_bits & 0x1 or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise Stop('chemin_archive_extension_refuse')
            if info.is_dir():continue
            total+=info.file_size
            if total>30_000_000:raise Stop('archive_extension_decompressee_trop_grande')
            suffix=path.suffix.lower()
            if suffix in EXECUTABLE_SUFFIXES:
                blocked.append(str(path));continue
            if suffix not in SAFE_TEXT_SUFFIXES:
                blocked.append(str(path));continue
            data=archive.read(info)
            if len(data)>1_000_000 or b'\x00' in data:
                blocked.append(str(path));continue
            try:text=data.decode('utf-8')
            except UnicodeError:
                blocked.append(str(path));continue
            texts[str(path)]=text
            inventory.append({'path':str(path),'bytes':len(data),
                              'sha256':hashlib.sha256(data).hexdigest()})
    except (zipfile.BadZipFile, RuntimeError):
        raise Stop('archive_extension_zip_invalide') from None
    skills=[(path,text) for path,text in texts.items() if path.lower().endswith('/skill.md') or path.lower()=='skill.md']
    if not skills:
        raise Stop('skill_md_absent_archive_extension')
    skill_text='\n\n'.join('### '+path+'\n'+text for path,text in skills)
    if len(skill_text)>200_000:raise Stop('instructions_extension_trop_longues')
    sha=hashlib.sha256(raw).hexdigest()
    target=_archive_dir(desk)/(extension_id+'-'+sha[:16]+'.zip')
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600) if not target.exists() else None
    if fd is not None:
        with os.fdopen(fd,'wb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
    item.update({'archive_path':str(target),'archive_sha256':sha,
                 'archive_inventory':inventory,'skill_text':skill_text,
                 'status':'review_required','enabled':False,'updated_at':_now(),
                 'security_review':{'declarative_files':len(inventory),
                   'blocked_files':blocked,'scripts_executed':False,
                   'message':('Archive inspectée. Les scripts et fichiers binaires sont bloqués ; '
                              'seules les instructions SKILL.md pourront être activées.')}})
    desk.setting('lawve:item:'+extension_id,item)
    desk.c.setdefault('lawve_extensions',{})[extension_id]=item
    desk.audit('archive_extension_lawve_importee',{
      'id':extension_id,'sha256':sha,'declarative_files':len(inventory),
      'blocked_files_count':len(blocked),'scripts_executed':False})
    return _public(item)


def _parse_mcp(raw):
    text=raw.decode('utf-8','strict').strip()
    if text.startswith('data:'):
        candidates=[]
        for line in text.splitlines():
            if line.startswith('data:'):
                try:candidates.append(json.loads(line[5:].strip()))
                except (ValueError,TypeError):continue
        if not candidates:raise Stop('reponse_mcp_sse_invalide')
        return candidates[-1]
    try:return json.loads(text)
    except (ValueError,UnicodeError):raise Stop('reponse_mcp_invalide') from None


_SESSIONS={}


def _mcp_call(item, method, params=None):
    """5.6.14 (C17) : appel JSON-RPC au travers d'une session MCP conservée par connecteur (initialize, notifications/initialized,
    identifiant Mcp-Session-Id renvoyé à chaque appel, flux d'événements acceptés)."""
    from . import mcp5614
    key=str(item.get('endpoint',''))+'|'+str(item.get('secret_file',''))
    session=_SESSIONS.get(key)
    if session is None or method=='initialize':
        session=mcp5614.Session(item);_SESSIONS[key]=session
    if method=='initialize':return session.initialize()
    if method=='notifications/initialized':return {}
    if not session.protocol:session.initialize()
    return session.call(method,params)


def test_item(desk, extension_id, transport=None, deep=False):
    extension_id=_identifier(extension_id)
    item=desk.settings('lawve:item:'+extension_id)
    if not item:raise Stop('extension_lawve_absente')
    started=__import__('time').monotonic()
    try:
        if item['kind']!='connector':
            if not item.get('archive_sha256') or not item.get('skill_text'):
                raise Stop('archive_extension_absente')
            result={'status':'review_required','message':'Archive déclarative lisible ; validation humaine requise avant activation.',
                    'files':len(item.get('archive_inventory',[])),'latency_ms':0}
        elif item.get('auth_type')=='oauth2' and not item.get('secret_file'):
            result={'status':'authentication_required',
                    'message':'Endpoint enregistré. Une autorisation OAuth 2.1 interactive reste nécessaire ; aucune donnée dossier n’a été envoyée.',
                    'endpoint':item['endpoint'],'latency_ms':0}
        else:
            # 5.6.14 (C17) : test de connexion = initialize + tools/list paginé via la session ; aucun outil exécuté. Les niveaux
            # « recherche » et « récupération du texte » sont testés séparément (deep=True, requête d'échantillon neutre).
            from . import mcp5614
            session=mcp5614.CallSession(item,_mcp_call)
            initialized=session.initialize();rows=session.list_tools()
            levels={'connection':'ok','search':'untested','text':'untested'}
            if deep:
                probe=mcp5614.diagnostic(item,call=_mcp_call)
                levels={k:probe[k] for k in ('connection','search','text')}
            result={'status':'ok','message':'Connexion MCP établie ; aucun outil n’a été exécuté et aucune donnée dossier n’a été envoyée.' if not deep else
                      'Connexion MCP établie ; recherche : %s ; récupération du texte : %s (requête d’échantillon neutre, aucune donnée dossier).' % (levels['search'],levels['text']),
                    'protocol_version':initialized.get('protocolVersion','') if isinstance(initialized,dict) else '',
                    'server':initialized.get('serverInfo',{}) if isinstance(initialized,dict) else {},'tools_count':len(rows),
                    'tools':[str(x.get('name',''))[:100] for x in rows[:100] if isinstance(x,dict)],
                    'tool_specs':[{'name':str(x.get('name',''))[:100],'description':str(x.get('description',''))[:500],
                      'inputSchema':x.get('inputSchema',{}) if isinstance(x.get('inputSchema',{}),dict) else {}} for x in rows[:100] if isinstance(x,dict)],
                    'levels':levels,'latency_ms':round((__import__('time').monotonic()-started)*1000)}
    except Stop as error:
        code=str(error)
        messages={'http_401':'Authentification refusée ou autorisation OAuth requise.',
          'http_403':'Accès refusé par le connecteur.',
          'http_404':'Endpoint MCP introuvable.',
          'connexion_http_indisponible':'Connecteur injoignable depuis le serveur.',
          'delai_http_depasse':'Le connecteur n’a pas répondu dans le délai imparti.'}
        result={'status':'error','error':code,'message':messages.get(code,'Test impossible : '+code.replace('_',' ')+'.'),
                'latency_ms':round((__import__('time').monotonic()-started)*1000)}
    item['last_test']={**result,'at':_now()};item['updated_at']=_now()
    if result['status']=='ok':item['status']='tested'
    desk.setting('lawve:item:'+extension_id,item)
    desk.c.setdefault('lawve_extensions',{})[extension_id]=item
    desk.audit('test_extension_lawve',{'id':extension_id,'status':result['status'],
                                      'error':result.get('error','')})
    return result


def set_enabled(desk, extension_id, enabled, reviewed=False):
    extension_id=_identifier(extension_id)
    item=desk.settings('lawve:item:'+extension_id)
    if not item:raise Stop('extension_lawve_absente')
    enabled=bool(enabled)
    if enabled:
        if not reviewed:raise Stop('revue_extension_explicite_requise')
        if item['kind']=='connector':
            if not item.get('external_data_allowed'):raise Stop('autorisation_donnees_externes_requise')
            if item.get('last_test',{}).get('status')!='ok':raise Stop('test_extension_requis')
        elif not item.get('archive_sha256') or not item.get('skill_text'):
            raise Stop('archive_extension_absente')
    item['enabled']=enabled;item['status']='enabled' if enabled else 'disabled';item['updated_at']=_now()
    desk.setting('lawve:item:'+extension_id,item)
    desk.c.setdefault('lawve_extensions',{})[extension_id]=item
    desk.audit('extension_lawve_activation',{'id':extension_id,'enabled':enabled,
      'reviewed':bool(reviewed),'scripts_executed':False})
    return _public(item)


def active_skill_instructions(config, purpose):
    blocks=[]
    for item in (config.get('lawve_extensions') or {}).values():
        if (not isinstance(item,dict) or not item.get('enabled') or
            item.get('kind') not in ('skill','plugin') or purpose not in item.get('purposes',[])):
            continue
        text=str(item.get('skill_text') or '')
        if text:
            blocks.append('Extension '+item.get('name',item.get('id',''))+' ['+item.get('archive_sha256','')[:16]+']\n'+text)
    combined='\n\n'.join(blocks)
    return combined[:80_000]


def active_extensions(config,purpose):
    """Return enabled extension metadata without secrets or instruction bodies."""
    result=[]
    for item in (config.get('lawve_extensions') or {}).values():
        if (isinstance(item,dict) and item.get('enabled') and
            purpose in item.get('purposes',[])):
            result.append({'id':item.get('id',''),'kind':item.get('kind',''),
              'name':item.get('name',''),'source_url':item.get('source_url',''),
              'tested':item.get('last_test',{}).get('status')=='ok'})
    return sorted(result,key=lambda x:x['id'])


def _legal_provider(item):
    value=' '.join(str(item.get(k,'')) for k in ('id','name','slug','author')).lower()
    for provider in ('openlegal','openlegi','goodlegal','pappers'):
        if provider in value:return provider
    return ''


def _tool_result_rows(result):
    """Decode structured MCP search output without executing a second tool."""
    if isinstance(result,dict) and isinstance(result.get('structuredContent'),dict):
        value=result['structuredContent']
    else:
        texts=[]
        for block in result.get('content',[]) if isinstance(result,dict) else []:
            if isinstance(block,dict) and block.get('type')=='text':texts.append(str(block.get('text','')))
        raw='\n'.join(texts)[:200_000]
        try:value=json.loads(raw)
        except (ValueError,TypeError):return []
    if isinstance(value,dict):value=value.get('results',value.get('items',value.get('decisions',[])))
    return value if isinstance(value,list) else []


def legal_connector_search(desk,anonymous_query,limit=8):
    """Call only an explicitly enabled legal search tool with an anonymised query."""
    if not 1<=len(str(anonymous_query))<=3000:raise Stop('requete_mcp_non_conforme')
    limit=max(1,min(int(limit),20));results=[]
    for item in (desk.c.get('lawve_extensions') or {}).values():
        if (not isinstance(item,dict) or item.get('kind')!='connector' or not item.get('enabled') or
            not item.get('external_data_allowed') or item.get('last_test',{}).get('status')!='ok' or
            not {'legal_analysis','hearing'}.intersection(item.get('purposes',[]))):continue
        provider=_legal_provider(item)
        if not provider:
            results.append({'connector':item.get('id',''),'provider':'','status':'skipped',
              'error':'connecteur_non_associe_a_un_fournisseur_juridique_verifiable','rows':[]});continue
        # 5.6.14 (C17, M13) : outil choisi et arguments construits d'après le schéma réel (champs imbriqués, pas de « query » supposé),
        # appel au travers de la session MCP du connecteur ; les schémas viennent du dernier test de connexion.
        from . import mcp5614
        specs=item.get('last_test',{}).get('tool_specs',[])
        found=mcp5614.search(item,anonymous_query,limit,call=_mcp_call,tools=specs if specs else None)
        results.append({'connector':item.get('id',''),'provider':provider,'status':found['status'],
          'tool':found.get('tool',''),'rows':found.get('rows',[]),'error':found.get('error',''),'session':found.get('session',False)})
    return results


def load_runtime_settings(desk):
    rows=desk.db.execute("SELECT key,value FROM settings WHERE key LIKE 'lawve:item:%'")
    _load_rows(desk.c,rows)


def _load_rows(config,rows):
    registry=config.setdefault('lawve_extensions',{})
    for row in rows:
        try:value=json.loads(row['value'])
        except (ValueError,TypeError):continue
        if isinstance(value,dict):registry[row['key'].split(':',2)[-1]]=value


def load_config_settings(config):
    path=Path(config.get('state_dir',''))/'desk.sqlite3'
    if not path.is_file():return
    db=None
    try:
        db=sqlite3.connect(path,timeout=2);db.row_factory=sqlite3.Row
        _load_rows(config,db.execute("SELECT key,value FROM settings WHERE key LIKE 'lawve:item:%'"))
    except sqlite3.Error:return
    finally:
        if db is not None:db.close()

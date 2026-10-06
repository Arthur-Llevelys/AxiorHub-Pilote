"""Small bounded primitives. No application credentials are handed to the model."""
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import ssl
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class Stop(Exception):
    """Public error codes only: exception messages never contain server replies."""


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def fold(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.lower())
                   if not unicodedata.combining(c))


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_secret(path):
    p = Path(path)
    if not p.is_file() or p.stat().st_mode & 0o027:
        raise Stop('secret_absent_ou_permissions_trop_larges')
    if p.stat().st_size>16384:raise Stop('secret_invalide')
    value = p.read_text(encoding='utf-8').strip()
    from .vault567 import decrypt
    value = decrypt(p,value)
    if not value or len(value) > 4096 or '\n' in value or '\r' in value:
        raise Stop('secret_invalide')
    return value


def xml_bytes(raw):
    if len(raw) > 12_000_000 or b'\x00' in raw or re.search(br'<!\s*(DOCTYPE|ENTITY)', raw, re.I):
        raise Stop('xml_refuse')
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise Stop('xml_invalide') from None


def clean_path(path):
    # Configuration and DAV paths are decoded once. Reject ambiguous encodings.
    if '\\' in path or any(ord(c) < 32 for c in path):
        raise Stop('chemin_refuse')
    parts = path.strip('/').split('/')
    if any(p in ('.', '..') for p in parts) or re.search(r'%[0-9a-f]{2}', path, re.I):
        raise Stop('chemin_refuse')
    return '/' + '/'.join(p for p in parts if p)


def under(path, root):
    path, root = clean_path(path), clean_path(root)
    return path == root or path.startswith(root.rstrip('/') + '/')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTP:
    def __init__(self, base, username=None, password=None, local_only=False, timeout=45, local_hosts=()):
        p = urllib.parse.urlsplit(base)
        if p.username or p.password or p.query or p.fragment or p.scheme not in ('https', 'http'):
            raise Stop('url_invalide')
        try:
            loopback = ipaddress.ip_address(p.hostname).is_loopback
        except ValueError:
            loopback = p.hostname in ('localhost','host.docker.internal')
        approved = p.hostname in set(local_hosts) if local_only else False
        if local_only and not (loopback or approved):
            raise Stop('ollama_doit_etre_local')
        if p.scheme != 'https' and not (loopback or approved):
            raise Stop('https_obligatoire')
        self.base, self.origin = base.rstrip('/'), (p.scheme, p.netloc)
        self.timeout = timeout
        self.headers = {}
        if username is not None:
            self.headers['Authorization'] = 'Basic ' + base64.b64encode(
                (username + ':' + password).encode()).decode()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def request(self, method, url, data=None, headers=None, limit=12_000_000):
        p = urllib.parse.urlsplit(url)
        if (p.scheme, p.netloc) != self.origin or p.username or p.password or p.fragment:
            raise Stop('origine_http_refusee')
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={**self.headers, **(headers or {})})
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                raw = r.read(limit + 1)
                if len(raw) > limit:
                    raise Stop('reponse_trop_volumineuse')
                return raw
        except urllib.error.HTTPError as e:
            raise Stop('http_' + str(e.code)) from None
        except TimeoutError:
            raise Stop('delai_http_depasse') from None
        except urllib.error.URLError as error:
            if isinstance(error.reason,TimeoutError):raise Stop('delai_http_depasse') from None
            raise Stop('connexion_http_indisponible') from None
        except OSError:
            raise Stop('connexion_http_indisponible') from None

    def json(self, method, path, data=None):
        raw = self.request(method, self.base + path,
                           None if data is None else json.dumps(data).encode(),
                           {'Content-Type': 'application/json'}, 2_000_000)
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise Stop('json_http_invalide') from None


def load_config(path):
    c = json.loads(Path(path).read_text(encoding='utf-8'))
    if c.get('mode') not in ('observe', 'drafts'):
        raise Stop('mode_invalide')
    if not c.get('mail', {}).get('own_addresses'):
        raise Stop('adresses_du_cabinet_absentes')
    if not c['mail'].get('sent') or not c['mail'].get('drafts'):
        raise Stop('dossiers_imap_absents')
    if c['mail']['drafts'] in (c['mail']['inbox'], c['mail']['sent']):
        raise Stop('dossiers_imap_confondus')
    if c['ollama']['model'].endswith('-cloud') or ':cloud' in c['ollama']['model']:
        raise Stop('modele_cloud_refuse')
    autonomy=c.get('autonomy',{})
    control_model=str(autonomy.get('control_model',''))
    if control_model.endswith('-cloud') or ':cloud' in control_model:
        raise Stop('modele_controle_cloud_refuse')
    routing=c.get('model_routing',{})
    try:
        for role in ('fast','complex','control'):
            name=str(routing.get(role+'_model',''))
            if name.endswith('-cloud') or ':cloud' in name:raise Stop('modele_cloud_refuse')
            temperature=float(routing.get(role+'_temperature',0))
            if temperature<0 or temperature>0.3:raise Stop('temperature_modele_invalide')
    except (TypeError,ValueError):raise Stop('temperature_modele_invalide') from None
    if autonomy:
        try:
            interval=int(autonomy.get('mail_monitor_interval_minutes',5))
            batch=int(autonomy.get('mail_batch_size',20))
            confidence=int(autonomy.get('automatic_matter_confidence_min',80))
        except (TypeError,ValueError):
            raise Stop('parametres_autonomie_invalides') from None
        if interval < 5 or interval > 1440 or batch < 1 or batch > 200 or confidence < 70 or confidence > 100:
            raise Stop('parametres_autonomie_invalides')
    if not c['nextcloud'].get('roots'):
        raise Stop('racines_nextcloud_absentes')
    for root in c['nextcloud']['roots']:
        if clean_path(root) == '/':
            raise Stop('racine_globale_refusee')
    for root in c['nextcloud'].get('matter_roots',[]):
        if clean_path(root)=='/' or not any(under(root,parent) for parent in c['nextcloud']['roots']):
            raise Stop('racine_dossiers_clients_hors_racines')
    if c.get('nextcloud_documents'):
        document_account=c['nextcloud_documents']
        if not document_account.get('roots') or any(clean_path(root)=='/' for root in document_account.get('roots',[])):
            raise Stop('racines_documents_nextcloud_invalides')
        if not all(document_account.get(key) for key in ('url','username','password_file')):
            raise Stop('compte_documents_nextcloud_incomplet')
    c['_path'] = str(Path(path).resolve())
    from .ai_gateway import load_config_settings
    load_config_settings(c)
    from .extensions364 import load_config_settings as load_extension_settings
    load_extension_settings(c)
    return c


def matter_display(matter):
    """Nom affiché d'un dossier : le nom exact du dossier Nextcloud (ex. « ALPHA - SAS EXEMPLE - CONSEIL - 20240101001 »).

    À défaut de chemin, « client — identifiant » comme avant la 5.2.0.
    """
    matter = matter or {}
    folder = PurePosixPath(str(matter.get('path', '')).rstrip('/')).name
    if folder:
        return folder
    return ' — '.join(x for x in (str(matter.get('client_name', '') or ''), str(matter.get('id', '') or '')) if x) or 'Dossier'


def load_matters(c):
    rows = json.loads(Path(c['matters_file']).read_text(encoding='utf-8'))
    if not isinstance(rows, list):
        raise Stop('registre_dossiers_invalide')
    # Browser-approved changes are separate from the administrator's original file.
    overlay = Path(c['state_dir']) / 'registry-web.json'
    if overlay.exists():
        merged = {r['id']: r for r in rows}
        for row in json.loads(overlay.read_text(encoding='utf-8')):
            merged[row['id']] = row
        rows = list(merged.values())
    ids = set()
    for r in rows:
        if not isinstance(r.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', r['id']) or r['id'] in ids:
            raise Stop('identifiant_dossier_invalide')
        ids.add(r['id'])
        if not any(under(r['path'], p) for p in c['nextcloud']['roots']):
            raise Stop('dossier_hors_racines')
        for person in r.get('correspondents', []):
            if person.get('role') not in VALID_ROLES:
                raise Stop('role_correspondant_invalide')
    return rows


VALID_ROLES = ('client', 'confrere_adverse', 'tiers', 'prospect', 'juridiction', 'expert', 'administration', 'commissaire_justice',
               'autre_partie', 'personnel')


def matter_scope_conflicts(matters):
    """List registered matter paths that contain another registered matter."""
    conflicts=[]
    for parent in matters:
        descendants=[child for child in matters if child['id']!=parent['id'] and
                     under(child['path'],parent['path']) and
                     clean_path(child['path'])!=clean_path(parent['path'])]
        if descendants:
            conflicts.append({'parent':parent['id'],'parent_path':clean_path(parent['path']),
                              'children':[x['id'] for x in descendants],
                              'child_paths':[clean_path(x['path']) for x in descendants]})
    return conflicts


def indexable_matters(c):
    """Exclude broad parent scopes to prevent cross-matter document leakage."""
    matters=load_matters(c);blocked={x['parent'] for x in matter_scope_conflicts(matters)}
    return [m for m in matters if m['id'] not in blocked]

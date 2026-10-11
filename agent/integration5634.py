"""5.6.34 (chapitre 9.6) : contrat versionné entre l'application Nextcloud « AxiorHub Pilote » et le moteur — ``/integration/v1``.

Désactivé par défaut. Activation : ``integration5634.enabled`` et un secret d'instance (fichier) partagé avec l'application Nextcloud.

Authentification : jamais un simple identifiant utilisateur joint à une clé globale. Chaque requête porte l'en-tête
``X-AxiorHub-Context`` = base64url(JSON) + « . » + HMAC-SHA256(secret, base64url(JSON)), le JSON contenant l'instance, l'utilisateur
Nextcloud (déduit côté serveur Nextcloud), le périmètre (dossiers autorisés), l'opération, une expiration (≤ 5 min) et un nonce à
usage unique (protection contre le rejeu). Le moteur revérifie le périmètre pour chaque dossier, travail et résultat.

Opérations : santé (sans secret), manifeste de sources (file_id, version/ETag, empreintes), travaux avec clé d'idempotence (contrainte
unique : deux requêtes concurrentes rejoignent le même travail), suivi, annulation, résultat, approbation d'une version précise de
résultat (conflit si le résultat a changé). Une erreur porte code, service, stage, retryable, correlation_id et message.
Un travail accepté ne signifie pas un document produit ; une approbation ne vaut pas pour les versions futures.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import uuid

from .common import Stop, load_matters, read_secret

CONTRACT = '1.0'
OPERATIONS = {'fiche_dossier': 'fiche_dossier5627', 'recherche_juridique': 'recherche5634'}
MAX_SKEW = timedelta(minutes=5)
MAX_BODY = 2_000_000


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS integration_nonces5634(nonce TEXT PRIMARY KEY, expires TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS integration_jobs5634(id TEXT PRIMARY KEY, instance TEXT NOT NULL, user TEXT NOT NULL, idem TEXT NOT NULL,
      operation TEXT NOT NULL, matter TEXT NOT NULL, manifest TEXT NOT NULL DEFAULT '', job INTEGER, created TEXT NOT NULL,
      UNIQUE(instance, user, idem));
    CREATE TABLE IF NOT EXISTS integration_manifests5634(id TEXT PRIMARY KEY, instance TEXT NOT NULL, user TEXT NOT NULL, matter TEXT NOT NULL,
      sources TEXT NOT NULL, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS integration_approvals5634(result TEXT NOT NULL, version TEXT NOT NULL, user TEXT NOT NULL, decision TEXT NOT NULL,
      at TEXT NOT NULL, PRIMARY KEY(result, version, user));''')


class Refused(Stop):
    def __init__(self, code, status='400 Bad Request', stage='controle', retryable=False):
        super().__init__(code)
        self.status, self.stage, self.retryable = status, stage, retryable


def _b64d(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def sign(secret, context):
    """Utilitaire (tests, documentation) : en-tête signé pour un contexte donné."""
    payload = base64.urlsafe_b64encode(json.dumps(context, separators=(',', ':'), sort_keys=True).encode()).decode().rstrip('=')
    return payload + '.' + hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _settings(cfg):
    s = cfg.get('integration5634') or {}
    if not s.get('enabled') or not s.get('secret_file'):
        raise Refused('integration_desactivee', '404 Not Found')
    return s


def verify(desk, header, now=None):
    """Contexte signé, non expiré, à usage unique, émis pour CETTE instance. Renvoie le contexte."""
    s = _settings(desk.c)
    secret = read_secret(s['secret_file'])
    try:
        payload, mac = str(header or '').split('.', 1)
    except ValueError:
        raise Refused('contexte_absent', '401 Unauthorized') from None
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        raise Refused('signature_invalide', '401 Unauthorized')
    try:
        ctx = json.loads(_b64d(payload))
    except (ValueError, UnicodeError):
        raise Refused('contexte_invalide', '401 Unauthorized') from None
    now = now or datetime.now(timezone.utc)
    try:
        exp = datetime.fromisoformat(str(ctx['exp']))
    except (KeyError, ValueError):
        raise Refused('contexte_invalide', '401 Unauthorized') from None
    if exp < now or exp > now + MAX_SKEW:
        raise Refused('contexte_expire', '401 Unauthorized')
    if ctx.get('instance') != s.get('instance_id') or not re.fullmatch(r'[\w.@+-]{1,120}', str(ctx.get('user') or '')):
        raise Refused('contexte_hors_instance', '403 Forbidden')
    nonce = str(ctx.get('nonce') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', nonce):
        raise Refused('nonce_invalide', '401 Unauthorized')
    schema(desk)
    desk.db.execute('DELETE FROM integration_nonces5634 WHERE expires<?', (now.isoformat(),))
    cur = desk.db.execute('INSERT OR IGNORE INTO integration_nonces5634 VALUES(?,?)', (nonce, (now + MAX_SKEW * 2).isoformat()))
    desk.db.commit()
    if not cur.rowcount:
        raise Refused('requete_rejouee', '401 Unauthorized')
    ctx['scope'] = [str(x) for x in (ctx.get('scope') or []) if isinstance(x, str)][:500]
    return ctx


def _in_scope(ctx, matter):
    if matter not in ctx['scope'] or not any(m['id'] == matter for m in load_matters_safe(ctx.get('_cfg'))):
        raise Refused('dossier_hors_perimetre', '403 Forbidden')


def load_matters_safe(cfg):
    try:
        return load_matters(cfg) if cfg else []
    except Stop:
        return []


def _job_state(row):
    return {'pending': 'pending', 'running': 'running', 'done': 'succeeded', 'error': 'failed', 'cancelled': 'cancelled',
            'cancel_requested': 'cancel_requested'}.get(row['status'], row['status'])


def handle(desk, method, path, headers, body):
    """Point d'entrée (appelé par la façade web) ; renvoie (statut HTTP, objet JSON)."""
    correlation = uuid.uuid4().hex[:12]
    try:
        if path == '/integration/v1/health' and method == 'GET':
            _settings(desk.c)
            from . import __version__
            return '200 OK', {'version': __version__, 'contract': CONTRACT, 'operations': sorted(OPERATIONS), 'status': 'ok'}
        ctx = verify(desk, headers.get('HTTP_X_AXIORHUB_CONTEXT'))
        ctx['_cfg'] = desk.c
        data = {}
        if method == 'POST':
            if len(body or b'') > MAX_BODY:
                raise Refused('requete_trop_longue', '413 Payload Too Large')
            try:
                data = json.loads((body or b'{}').decode('utf-8') or '{}')
            except (ValueError, UnicodeError):
                raise Refused('json_invalide') from None
            if not isinstance(data, dict):
                raise Refused('objet_json_requis')
        if path == '/integration/v1/source-manifests' and method == 'POST':
            return '200 OK', manifest(desk, ctx, data)
        if path == '/integration/v1/jobs' and method == 'POST':
            return '202 Accepted', create_job(desk, ctx, data)
        m = re.fullmatch(r'/integration/v1/jobs/([a-f0-9]{32})(/cancel)?', path)
        if m and method == ('POST' if m.group(2) else 'GET'):
            return '200 OK', (cancel_job(desk, ctx, m.group(1)) if m.group(2) else job_status(desk, ctx, m.group(1)))
        m = re.fullmatch(r'/integration/v1/results/([a-f0-9]{32})(/approvals)?', path)
        if m and method == ('POST' if m.group(2) else 'GET'):
            return ('200 OK', approve(desk, ctx, m.group(1), data)) if m.group(2) else ('200 OK', result(desk, ctx, m.group(1)))
        raise Refused('route_inconnue', '404 Not Found')
    except Refused as ex:
        return ex.status, _error(str(ex), ex.stage, ex.retryable, correlation)
    except Stop as ex:
        return '400 Bad Request', _error(str(ex), 'traitement', False, correlation)


def _error(code, stage, retryable, correlation):
    from .erreurs5634 import explain
    return {'code': code, 'service': 'axiorhub', 'stage': stage, 'retryable': retryable, 'correlation_id': correlation, 'message': explain(code)}


def manifest(desk, ctx, data):
    matter = str(data.get('matter') or '')
    _in_scope(ctx, matter)
    sources = data.get('sources') or []
    if not isinstance(sources, list) or len(sources) > 500:
        raise Refused('manifeste_invalide')
    clean, errors = [], []
    for s in sources:
        if not isinstance(s, dict) or not re.fullmatch(r'\d{1,20}', str(s.get('file_id') or '')):
            errors.append({'source': str(s)[:80], 'error': 'file_id_invalide'})
            continue
        clean.append({'file_id': str(s['file_id']), 'etag': str(s.get('etag') or '')[:80], 'size': int(s.get('size') or 0),
                      'sha256': str(s.get('sha256') or '')[:64], 'name': str(s.get('name') or '')[:255]})
    ident = uuid.uuid4().hex
    schema(desk)
    desk.db.execute('INSERT INTO integration_manifests5634 VALUES(?,?,?,?,?,?)', (ident, ctx['instance'], ctx['user'], matter,
                                                                              json.dumps(clean, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
    desk.db.commit()
    return {'manifest_id': ident, 'accepted': len(clean), 'errors': errors, 'coverage': {'declared': len(sources), 'accepted': len(clean)}}


def create_job(desk, ctx, data):
    op = str(data.get('operation') or '')
    if op not in OPERATIONS:
        raise Refused('operation_inconnue')
    idem = str(data.get('idempotency_key') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', idem):
        raise Refused('cle_idempotence_requise')
    matter = str(data.get('matter') or '')
    if op == 'fiche_dossier' or matter:
        _in_scope(ctx, matter)
    schema(desk)
    ident = uuid.uuid4().hex
    cur = desk.db.execute('INSERT OR IGNORE INTO integration_jobs5634(id,instance,user,idem,operation,matter,manifest,job,created) VALUES(?,?,?,?,?,?,?,NULL,?)',
                          (ident, ctx['instance'], ctx['user'], idem, op, matter, str(data.get('manifest_id') or ''), datetime.now(timezone.utc).isoformat()))
    desk.db.commit()
    if not cur.rowcount:   # même clé : on rejoint le travail existant (aucun doublon)
        row = desk.db.execute('SELECT * FROM integration_jobs5634 WHERE instance=? AND user=? AND idem=?', (ctx['instance'], ctx['user'], idem)).fetchone()
        return {'job_id': row['id'], 'state': 'existing', 'status_url': '/integration/v1/jobs/' + row['id']}
    if op == 'fiche_dossier':
        from .dossier5627 import request
        out = request(desk, matter)
        job = out['job_id']
    else:
        question = str(data.get('question') or '').strip()
        if not 3 <= len(question) <= 3000:
            raise Refused('question_juridique_invalide')
        job = desk.enqueue('recherche5634', {'question': question, 'matter': matter, 'providers': []})
    desk.db.execute('UPDATE integration_jobs5634 SET job=? WHERE id=?', (job, ident))
    desk.db.commit()
    return {'job_id': ident, 'state': 'pending', 'status_url': '/integration/v1/jobs/' + ident}


def _own(desk, ctx, ident):
    schema(desk)
    row = desk.db.execute('SELECT * FROM integration_jobs5634 WHERE id=?', (ident,)).fetchone()
    if not row or row['instance'] != ctx['instance'] or row['user'] != ctx['user']:
        raise Refused('travail_absent', '404 Not Found')
    if row['matter']:
        _in_scope(ctx, row['matter'])   # droits recontrôlés à chaque lecture (perte de droits = accès refusé)
    return row


def job_status(desk, ctx, ident):
    row = _own(desk, ctx, ident)
    job = desk.db.execute('SELECT status,progress,result FROM jobs WHERE id=?', (row['job'],)).fetchone() if row['job'] else None
    if not job:
        return {'job_id': ident, 'state': 'pending', 'stage': '', 'partial': None, 'errors': []}
    res = json.loads(job['result'] or '{}') or {}
    errors = [_error(res.get('erreur'), (res.get('detail') or {}).get('stage', 'traitement'), bool((res.get('detail') or {}).get('retryable')), (res.get('detail') or {}).get('correlation_id', ''))] if job['status'] == 'error' else []
    return {'job_id': ident, 'state': _job_state(job), 'stage': job['progress'] or '', 'result_id': ident if job['status'] == 'done' else '', 'errors': errors}


def cancel_job(desk, ctx, ident):
    row = _own(desk, ctx, ident)
    try:
        out = desk.cancel_job(row['job']) if row['job'] else {'status': 'cancelled'}
    except Stop:
        out = {'status': 'deja_termine'}
    return {'job_id': ident, 'state': out['status'], 'message': 'Annulation enregistrée ; un résultat déjà produit est conservé.'}


def result(desk, ctx, ident):
    row = _own(desk, ctx, ident)
    job = desk.db.execute('SELECT status,result FROM jobs WHERE id=?', (row['job'],)).fetchone() if row['job'] else None
    if not job or job['status'] != 'done':
        raise Refused('resultat_indisponible', '409 Conflict', retryable=True)
    payload = json.loads(job['result'] or '{}') or {}
    if row['operation'] == 'fiche_dossier':
        from .dossier5627 import latest
        f = latest(desk, row['matter']) or {}
        payload = {'version': f.get('version'), 'data': f.get('data'), 'sources': f.get('sources'), 'corrections': f.get('edits')}
    version = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]
    return {'result_id': ident, 'version': version, 'operation': row['operation'], 'content': payload,
            'validations_attendues': ['relecture par l’avocat'], 'note': 'Une approbation ne vaut que pour cette version.'}


def approve(desk, ctx, ident, data):
    current = result(desk, ctx, ident)
    if str(data.get('version') or '') != current['version']:
        raise Refused('resultat_modifie_depuis_lecture', '409 Conflict')
    decision = str(data.get('decision') or '')
    if decision not in ('approuve', 'refuse'):
        raise Refused('decision_invalide')
    desk.db.execute('INSERT OR REPLACE INTO integration_approvals5634 VALUES(?,?,?,?,?)', (ident, current['version'], ctx['user'], decision,
                                                                                         datetime.now(timezone.utc).isoformat()))
    desk.db.commit()
    desk.audit('integration5634_approbation', {'result': ident, 'version': current['version'], 'decision': decision})
    return {'result_id': ident, 'version': current['version'], 'decision': decision}

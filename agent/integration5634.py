"""5.6.34 (chapitre 9.6) : contrat versionné entre l'application Nextcloud « AxiorHub Pilote » et le moteur — ``/integration/v1``.

Désactivé par défaut. Activation : ``integration5634.enabled`` et un secret d'instance (fichier) partagé avec l'application Nextcloud.

Authentification : jamais un simple identifiant utilisateur joint à une clé globale. Chaque requête porte l'en-tête
``X-AxiorHub-Context`` = base64url(JSON) + « . » + HMAC-SHA256(secret, base64url(JSON)), le JSON contenant l'instance, l'utilisateur
Nextcloud (déduit côté serveur Nextcloud), le périmètre (dossiers autorisés), l'opération, une expiration (≤ 5 min) et un nonce à
usage unique (protection contre le rejeu). Le moteur revérifie le périmètre pour chaque dossier, travail et résultat.

Opérations : santé (sans secret), liste des dossiers (administrateur Nextcloud, périmètre « * »), manifeste de sources (file_id,
version/ETag, empreintes), travaux avec clé d'idempotence, suivi, annulation, résultat, décision sur une version précise de résultat.
Une erreur porte code, service, stage, retryable, correlation_id et message.

5.6.35 (audit F08–F13) :
- F10 : l'opération signée (« MÉTHODE /chemin ») est comparée exactement à la route appelée, avant toute écriture ;
- F13 : contexte validé par un schéma strict (types, tailles, expiration avec fuseau) ; toute anomalie donne une erreur 4xx structurée ;
- F08 : la demande est validée AVANT la réservation de la clé ; réservation et mise en file dans une seule transaction ; empreinte du
  contenu : même clé + même contenu = même travail, même clé + autre contenu = 409 ; les réservations orphelines sont reprises ou closes ;
- F09 : état persistant du travail d'intégration (annulation durable, même sans travail moteur) ;
- F11 : le manifeste est contrôlé (existence, instance, utilisateur, dossier, fraîcheur) et devient le filtre réel des pièces analysées ;
- F12 : le résultat d'un travail est figé à sa première lecture (version de fiche produite par CE travail) ; l'approbation porte sur ce
  contenu exact, contrôlée dans la transaction ; décisions approuvé / refusé / correction demandée.
Secret partagé : 8 caractères au moins (même règle que l'application Nextcloud) ; 32 caractères aléatoires recommandés.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import sqlite3
import json
from pathlib import PurePosixPath
import re
import uuid

from .common import Stop, load_matters, matter_display, read_secret

CONTRACT = '1.2'   # 5.6.36 : clés personnelles, instruction au Pilote, recherche dans les dossiers
OPERATIONS = {'fiche_dossier': 'fiche_dossier5627', 'recherche_juridique': 'recherche5634', 'instruction': 'mission567'}
MAX_SKEW = timedelta(minutes=5)
MAX_BODY = 2_000_000
MANIFEST_TTL = timedelta(hours=24)
SECRET_MIN = 8
DECISIONS = ('approuve', 'refuse', 'correction_demandee')


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS integration_nonces5634(nonce TEXT PRIMARY KEY, expires TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS integration_jobs5634(id TEXT PRIMARY KEY, instance TEXT NOT NULL, user TEXT NOT NULL, idem TEXT NOT NULL,
      operation TEXT NOT NULL, matter TEXT NOT NULL, manifest TEXT NOT NULL DEFAULT '', job INTEGER, created TEXT NOT NULL,
      UNIQUE(instance, user, idem));
    CREATE TABLE IF NOT EXISTS integration_manifests5634(id TEXT PRIMARY KEY, instance TEXT NOT NULL, user TEXT NOT NULL, matter TEXT NOT NULL,
      sources TEXT NOT NULL, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS integration_approvals5634(result TEXT NOT NULL, version TEXT NOT NULL, user TEXT NOT NULL, decision TEXT NOT NULL,
      at TEXT NOT NULL, PRIMARY KEY(result, version, user));
    CREATE TABLE IF NOT EXISTS integration_decisions5635(id INTEGER PRIMARY KEY AUTOINCREMENT, result TEXT NOT NULL, version TEXT NOT NULL,
      user TEXT NOT NULL, decision TEXT NOT NULL, at TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '');
    CREATE INDEX IF NOT EXISTS integration_decisions5635_result ON integration_decisions5635(result, id);''')
    _columns(desk, 'integration_jobs5634', (('state', "TEXT NOT NULL DEFAULT 'pending'"), ('content_hash', "TEXT NOT NULL DEFAULT ''"),
                                            ('args', "TEXT NOT NULL DEFAULT '{}'"), ('error', "TEXT NOT NULL DEFAULT ''"),
                                            ('snapshot', "TEXT NOT NULL DEFAULT ''"), ('snapshot_version', "TEXT NOT NULL DEFAULT ''")))
    _columns(desk, 'integration_manifests5634', (('paths', "TEXT NOT NULL DEFAULT '[]'"),))
    _columns(desk, 'integration_approvals5634', (('comment', "TEXT NOT NULL DEFAULT ''"),))
    _columns(desk, 'integration_jobs5634', (('mission', "TEXT NOT NULL DEFAULT ''"),))   # 5.6.36 : mission du Pilote
    from . import integration5636
    integration5636.schema(desk)


def _columns(desk, table, cols):
    have = {r[1] for r in desk.db.execute('PRAGMA table_info(%s)' % table)}
    for name, decl in cols:
        if name not in have:
            desk.db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, name, decl))


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


def _secret(s):
    secret = read_secret(s['secret_file'])
    if len(secret) < SECRET_MIN:
        raise Refused('secret_integration_trop_court', '503 Service Unavailable', stage='configuration')
    return secret


def _exp(value):
    """F13 : date ISO 8601 AVEC fuseau ; une date naïve ou mal formée est refusée (jamais comparée)."""
    if not isinstance(value, str) or len(value) > 40:
        raise Refused('contexte_invalide', '401 Unauthorized')
    try:
        exp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise Refused('contexte_invalide', '401 Unauthorized') from None
    if exp.tzinfo is None or exp.utcoffset() is None:
        raise Refused('contexte_sans_fuseau', '401 Unauthorized')
    return exp.astimezone(timezone.utc)


def verify(desk, header, op=None, now=None, key_id=''):
    """Contexte signé, bien formé, non expiré, à usage unique, émis pour CETTE instance et pour CETTE opération. Renvoie le contexte."""
    from . import integration5636
    key = integration5636.key_secret(desk, key_id) if key_id else None
    if key_id and key is None:
        raise Refused('cle_personnelle_revoquee_ou_inconnue', '401 Unauthorized')
    if key is None:
        s = _settings(desk.c)
        secret = _secret(s)
    else:
        if not integration5636.enabled(desk):
            raise Refused('integration_desactivee', '404 Not Found')
        s, secret = {'instance_id': 'personnel:' + key['axiorhub_user']}, key['secret']
    header = header if isinstance(header, str) else ''
    if len(header) > 20000 or '.' not in header:
        raise Refused('contexte_absent', '401 Unauthorized')
    payload, mac = header.split('.', 1)
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        raise Refused('signature_invalide', '401 Unauthorized')
    try:
        ctx = json.loads(_b64d(payload))
    except (ValueError, UnicodeError, TypeError):
        raise Refused('contexte_invalide', '401 Unauthorized') from None
    if not isinstance(ctx, dict):
        raise Refused('contexte_invalide', '401 Unauthorized')
    now = now or datetime.now(timezone.utc)
    exp = _exp(ctx.get('exp'))
    if exp < now or exp > now + MAX_SKEW:
        raise Refused('contexte_expire', '401 Unauthorized')
    if not isinstance(ctx.get('instance'), str) or ctx['instance'] != s.get('instance_id'):
        raise Refused('contexte_hors_instance', '403 Forbidden')
    if not isinstance(ctx.get('user'), str) or not re.fullmatch(r'[\w.@+-]{1,120}', ctx['user']):
        raise Refused('contexte_hors_instance', '403 Forbidden')
    scope = ctx.get('scope', [])
    if not isinstance(scope, list) or len(scope) > 5000 or not all(isinstance(x, str) and 0 < len(x) <= 120 for x in scope):
        raise Refused('contexte_invalide', '401 Unauthorized')
    signed_op = ctx.get('op')
    if not isinstance(signed_op, str) or len(signed_op) > 300:
        raise Refused('operation_non_signee', '403 Forbidden')
    if op is not None and signed_op != op:   # F10 : un contexte signé pour lire un travail ne permet ni d'en créer ni d'en annuler un
        raise Refused('operation_non_signee', '403 Forbidden')
    nonce = ctx.get('nonce')
    if not isinstance(nonce, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', nonce):
        raise Refused('nonce_invalide', '401 Unauthorized')
    schema(desk)
    desk.db.execute('DELETE FROM integration_nonces5634 WHERE expires<?', (now.isoformat(),))
    cur = desk.db.execute('INSERT OR IGNORE INTO integration_nonces5634 VALUES(?,?)', (nonce, (now + MAX_SKEW * 2).isoformat()))
    desk.db.commit()
    if not cur.rowcount:
        raise Refused('requete_rejouee', '401 Unauthorized')
    ctx['scope'] = list(scope)
    if key is not None:   # 5.6.36 : le périmètre est celui du compte AxiorHub relié (le cabinet entier), pas celui déclaré par Nextcloud
        ctx.update(scope=['*'], _key=key['id'], _owner=key['axiorhub_user'], _role=key['role'])
        integration5636.touch(desk, key['id'])
    else:
        ctx.update(_owner='cabinet', _role='administrateur')
    return ctx


def _in_scope(ctx, matter):
    allowed = ctx['scope'] == ['*'] or matter in ctx['scope']   # 5.6.36 : « * » = tout le cabinet (compte AxiorHub relié)
    if not isinstance(matter, str) or not allowed or not any(m['id'] == matter for m in load_matters_safe(ctx.get('_cfg'))):
        raise Refused('dossier_hors_perimetre', '403 Forbidden')


def load_matters_safe(cfg):
    try:
        return load_matters(cfg) if cfg else []
    except Stop:
        return []


def _job_state(status):
    return {'pending': 'pending', 'running': 'running', 'done': 'succeeded', 'error': 'failed', 'cancelled': 'cancelled',
            'cancel_requested': 'cancel_requested'}.get(status, status)


def handle(desk, method, path, headers, body):
    """Point d'entrée (appelé par la façade web) ; renvoie (statut HTTP, objet JSON)."""
    correlation = uuid.uuid4().hex[:12]
    try:
        from . import integration5636
        if path == '/integration/v1/health' and method == 'GET':
            if not integration5636.enabled(desk):
                raise Refused('integration_desactivee', '404 Not Found')
            from . import __version__
            return '200 OK', {'version': __version__, 'contract': CONTRACT, 'operations': sorted(OPERATIONS), 'status': 'ok',
                              'personal_accounts': bool(desk.settings(integration5636.SETTING, False))}
        if path == '/integration/v1/pair' and method == 'POST':   # 5.6.36 : seule route non signée qui écrit (identifiants vérifiés)
            if len(body or b'') > 20000:
                raise Refused('requete_trop_longue', '413 Payload Too Large')
            try:
                data = json.loads((body or b'{}').decode('utf-8') or '{}')
            except (ValueError, UnicodeError):
                raise Refused('json_invalide') from None
            if not isinstance(data, dict):
                raise Refused('objet_json_requis')
            return '200 OK', integration5636.pair(desk, headers, data)
        ctx = verify(desk, headers.get('HTTP_X_AXIORHUB_CONTEXT'), op=method + ' ' + path, key_id=str(headers.get('HTTP_X_AXIORHUB_KEY') or ''))
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
        if path == '/integration/v1/matters' and method == 'GET':
            return '200 OK', matters(desk, ctx)
        if path == '/integration/v1/me' and method == 'GET':
            return '200 OK', integration5636.me(desk, ctx)
        if path == '/integration/v1/unpair' and method == 'POST':
            return '200 OK', integration5636.unpair(desk, ctx)
        if path == '/integration/v1/search' and method == 'GET':
            from urllib.parse import parse_qs
            q = {k: v[0] for k, v in parse_qs(str(headers.get('QUERY_STRING', ''))).items()}
            return '200 OK', integration5636.search(desk, ctx, q)
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
    except (TypeError, ValueError, KeyError, AttributeError) as ex:   # F13 : jamais de trace brute vers le client
        _log(desk, correlation, ex)
        return '400 Bad Request', _error('requete_invalide', 'controle', False, correlation)
    except sqlite3.OperationalError as ex:   # 5.6.36 : base momentanément occupée → JSON réessayable, jamais la page HTML d'attente
        if 'locked' not in str(ex).lower() and 'busy' not in str(ex).lower():
            raise
        try:
            desk.db.rollback()
        except sqlite3.Error:
            pass
        return '503 Service Unavailable', _error('moteur_occupe', 'base', True, correlation)


def _log(desk, correlation, ex):
    try:
        desk.audit('integration5634_erreur', {'correlation_id': correlation, 'type': type(ex).__name__})
    except Exception:   # noqa: BLE001
        pass


def _error(code, stage, retryable, correlation):
    from .erreurs5634 import explain
    return {'code': code, 'service': 'axiorhub', 'stage': stage, 'retryable': retryable, 'correlation_id': correlation, 'message': explain(code)}


# ------------------------------------------------------------------------------------------------ dossiers (application Nextcloud)
def matters(desk, ctx):
    """Liste des dossiers du moteur pour l'administrateur Nextcloud (périmètre « * » signé côté serveur Nextcloud) : identifiant,
    intitulé et état ; aucune pièce ni donnée de fond."""
    if ctx['scope'] != ['*']:
        raise Refused('liste_reservee_administrateur', '403 Forbidden')
    out = []
    for m in load_matters_safe(desk.c):
        out.append({'id': str(m.get('id') or ''), 'label': matter_display(m)[:255], 'archived': bool(m.get('archived'))})
    return {'matters': [x for x in out if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', x['id'])], 'total': len(out)}


# ------------------------------------------------------------------------------------------------ manifeste (F11)
def _norm_etag(value):
    return re.sub(r'^(W/)?"|"$', '', str(value or '').strip())


def manifest(desk, ctx, data):
    matter = data.get('matter')
    _in_scope(ctx, matter)
    sources = data.get('sources') or []
    if not isinstance(sources, list) or len(sources) > 500:
        raise Refused('manifeste_invalide')
    clean, errors = [], []
    for s in sources:
        if not isinstance(s, dict) or not re.fullmatch(r'\d{1,20}', str(s.get('file_id') or '')):
            errors.append({'source': str(s)[:80], 'error': 'file_id_invalide'})
            continue
        size = s.get('size', 0)
        if not isinstance(size, int) or not 0 <= size <= 10 ** 12:
            errors.append({'source': str(s.get('file_id')), 'error': 'taille_invalide'})
            continue
        sha = str(s.get('sha256') or '')
        if sha and not re.fullmatch(r'[a-f0-9]{64}', sha):
            errors.append({'source': str(s.get('file_id')), 'error': 'empreinte_invalide'})
            continue
        clean.append({'file_id': str(s['file_id']), 'etag': str(s.get('etag') or '')[:80], 'size': size, 'sha256': sha,
                      'name': PurePosixPath(str(s.get('name') or '').replace('\\', '/')).name[:255]})
    # Pont file_id → pièce du moteur : ETag de la version (puis nom exact s'il est unique). Une pièce introuvable est signalée ;
    # aucune autre pièce ne la remplace.
    from . import synthese5632
    inv = synthese5632.inventory(desk, matter)
    by_etag = {_norm_etag(d['etag']): d for d in inv if d['etag']}
    by_name = {}
    for d in inv:
        by_name.setdefault(PurePosixPath(d['path']).name, []).append(d)
    paths, accepted = [], []
    for s in clean:
        d = by_etag.get(_norm_etag(s['etag'])) if s['etag'] else None
        if d is None and len(by_name.get(s['name'], [])) == 1:
            d = by_name[s['name']][0]
        if d is None:
            errors.append({'source': s['file_id'], 'name': s['name'], 'error': 'piece_absente_de_l_index_du_dossier'})
            continue
        if d['state'] != 'extrait':
            errors.append({'source': s['file_id'], 'name': s['name'], 'error': 'piece_' + d['state']})
            continue
        paths.append(d['path'])
        accepted.append(s)
    ident = uuid.uuid4().hex
    schema(desk)
    desk.db.execute('INSERT INTO integration_manifests5634(id,instance,user,matter,sources,created,paths) VALUES(?,?,?,?,?,?,?)',
                    (ident, ctx['instance'], ctx['user'], matter, json.dumps(accepted, ensure_ascii=False), datetime.now(timezone.utc).isoformat(),
                     json.dumps(sorted(set(paths)), ensure_ascii=False)))
    desk.db.commit()
    return {'manifest_id': ident, 'accepted': len(accepted), 'errors': errors,
            'coverage': {'declared': len(sources), 'accepted': len(accepted), 'refused': len(errors)}}


def _manifest_paths(desk, ctx, matter, ident):
    if not ident:
        return None
    if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{32}', ident):
        raise Refused('manifeste_invalide')
    row = desk.db.execute('SELECT * FROM integration_manifests5634 WHERE id=?', (ident,)).fetchone()
    if not row or row['instance'] != ctx['instance'] or row['user'] != ctx['user'] or row['matter'] != matter:
        raise Refused('manifeste_absent_ou_hors_perimetre', '403 Forbidden')
    if datetime.fromisoformat(row['created']) < datetime.now(timezone.utc) - MANIFEST_TTL:
        raise Refused('manifeste_perime', '409 Conflict')
    paths = json.loads(row['paths'] or '[]')
    if not paths:
        raise Refused('aucune_piece_selectionnee_lisible', '409 Conflict')
    return paths


# ------------------------------------------------------------------------------------------------ travaux (F08, F09)
def _validate(desk, ctx, data):
    """Toute la demande est contrôlée AVANT la moindre réservation (F08)."""
    op = data.get('operation')
    if op not in OPERATIONS:
        raise Refused('operation_inconnue')
    idem = data.get('idempotency_key')
    if not isinstance(idem, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', idem):
        raise Refused('cle_idempotence_requise')
    matter = data.get('matter') or ''
    if not isinstance(matter, str):
        raise Refused('dossier_hors_perimetre', '403 Forbidden')
    if op == 'fiche_dossier' or matter:
        _in_scope(ctx, matter)
    manifest_id = data.get('manifest_id') or ''
    args = {'operation': op, 'matter': matter, 'manifest_id': manifest_id}
    if op == 'instruction':   # 5.6.36 : mission du Pilote (réponse, analyse, projet, brouillon déposé ; jamais d'envoi)
        from . import integration5636
        question, intent = integration5636.validate_instruction(data)
        if manifest_id:
            raise Refused('manifeste_non_applicable')
        args.update(question=question, intent=intent, idem=idem)
    elif op == 'recherche_juridique':
        question = data.get('question')
        question = question.strip() if isinstance(question, str) else ''
        if not 3 <= len(question) <= 3000:
            raise Refused('question_juridique_invalide')
        if manifest_id:
            raise Refused('manifeste_non_applicable')
        args['question'] = question
    else:
        schema(desk)
        args['only'] = _manifest_paths(desk, ctx, matter, manifest_id)
    content = hashlib.sha256(json.dumps({k: args[k] for k in ('operation', 'matter', 'manifest_id', 'question', 'intent') if k in args},
                                        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return idem, args, content


def _enqueue(desk, args):
    if args['operation'] == 'fiche_dossier':
        from .dossier5627 import request
        return request(desk, args['matter'], only=args.get('only'))['job_id']
    return desk.enqueue('recherche5634', {'question': args['question'], 'matter': args['matter'], 'providers': []})


def create_job(desk, ctx, data):
    idem, args, content = _validate(desk, ctx, data)
    schema(desk)
    _repair(desk)
    if not desk.db.in_transaction:
        desk.db.execute('BEGIN IMMEDIATE')
    try:
        row = desk.db.execute('SELECT * FROM integration_jobs5634 WHERE instance=? AND user=? AND idem=?', (ctx['instance'], ctx['user'], idem)).fetchone()
        if row is not None:
            if row['content_hash'] and row['content_hash'] != content:
                raise Refused('cle_idempotence_deja_utilisee', '409 Conflict')
            if row['job'] is not None or row['mission'] or row['state'] in ('cancelled', 'failed'):
                desk.db.commit()
                return {'job_id': row['id'], 'state': 'existing', 'status_url': '/integration/v1/jobs/' + row['id']}
            ident = row['id']   # réservation sans travail (ancienne version) : la demande valide la reprend
            desk.db.execute('UPDATE integration_jobs5634 SET content_hash=?, args=? WHERE id=?', (content, json.dumps(args, ensure_ascii=False), ident))
        else:
            ident = uuid.uuid4().hex
            desk.db.execute('INSERT INTO integration_jobs5634(id,instance,user,idem,operation,matter,manifest,job,created,state,content_hash,args) '
                            "VALUES(?,?,?,?,?,?,?,NULL,?,'pending',?,?)",
                            (ident, ctx['instance'], ctx['user'], idem, args['operation'], args['matter'], args['manifest_id'],
                             datetime.now(timezone.utc).isoformat(), content, json.dumps(args, ensure_ascii=False)))
        mission = ''
        if args['operation'] == 'instruction':
            from . import integration5636
            job, mission = integration5636.start_instruction(desk, ctx, args)
        else:
            job = _enqueue(desk, args)   # même transaction : la réservation n'est validée qu'avec son travail
        if not desk.db.in_transaction:
            desk.db.execute('BEGIN IMMEDIATE')
        desk.db.execute('UPDATE integration_jobs5634 SET job=?, mission=? WHERE id=?', (job, mission or '', ident))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    return {'job_id': ident, 'state': 'pending', 'status_url': '/integration/v1/jobs/' + ident}


def _repair(desk):
    """Réservations sans travail (interruption entre réservation et mise en file, ou versions 5.6.34) : reprise si la demande est
    complète, sinon clôture explicite « échec » avec motif."""
    limit = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    for row in desk.db.execute("SELECT id,args FROM integration_jobs5634 WHERE job IS NULL AND mission='' AND state='pending' AND created<?", (limit,)).fetchall():
        args = json.loads(row['args'] or '{}')
        try:
            if args.get('operation') not in OPERATIONS or args.get('operation') == 'instruction':
                raise Stop('demande_incomplete')
            job = _enqueue(desk, args)
            desk.db.execute('UPDATE integration_jobs5634 SET job=? WHERE id=?', (job, row['id']))
        except Stop as ex:
            desk.db.execute("UPDATE integration_jobs5634 SET state='failed', error=? WHERE id=?", ('reservation_sans_travail:' + str(ex), row['id']))
    desk.db.commit()


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
    if row['mission']:
        from . import integration5636
        return integration5636.instruction_status(desk, ctx, row)
    if row['state'] in ('cancelled', 'failed') and not row['job']:
        errors = [_error(row['error'].split(':')[0] or 'travail_non_cree', 'reservation', True, '')] if row['state'] == 'failed' else []
        return {'job_id': ident, 'state': row['state'], 'stage': '', 'result_id': '', 'errors': errors}
    job = desk.db.execute('SELECT status,progress,result FROM jobs WHERE id=?', (row['job'],)).fetchone() if row['job'] else None
    if not job:
        return {'job_id': ident, 'state': 'pending', 'stage': '', 'result_id': '', 'errors': []}
    res = json.loads(job['result'] or '{}') or {}
    detail = res.get('detail') or {}
    errors = [_error(res.get('erreur'), detail.get('stage', 'traitement'), bool(detail.get('retryable')), detail.get('correlation_id', ''))] if job['status'] == 'error' else []
    state = _job_state(job['status'])
    if row['state'] == 'cancelled' and state in ('pending', 'running'):
        state = 'cancel_requested'
    return {'job_id': ident, 'state': state, 'stage': job['progress'] or '', 'result_id': ident if job['status'] == 'done' else '', 'errors': errors}


def cancel_job(desk, ctx, ident):
    row = _own(desk, ctx, ident)
    if not row['job']:
        if row['state'] == 'failed':
            return {'job_id': ident, 'state': 'already_finished', 'message': 'Ce travail n’a pas pu être créé : rien à annuler.'}
        desk.db.execute("UPDATE integration_jobs5634 SET state='cancelled' WHERE id=? AND job IS NULL", (ident,))   # F09 : état durable
        desk.db.commit()
        return {'job_id': ident, 'state': 'cancelled', 'message': 'Annulation enregistrée : le travail ne sera pas lancé.'}
    try:
        out = desk.cancel_job(row['job'])
    except Stop:
        return {'job_id': ident, 'state': 'already_finished', 'message': 'Le travail était déjà terminé : son résultat est conservé.'}
    desk.db.execute("UPDATE integration_jobs5634 SET state='cancelled' WHERE id=?", (ident,))
    desk.db.commit()
    return {'job_id': ident, 'state': out['status'], 'message': 'Annulation enregistrée ; un résultat déjà produit est conservé.'}


# ------------------------------------------------------------------------------------------------ résultat figé et décision (F12)
def _hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def _decisions(desk, ident):
    """0.2.0 de l'app / 5.6.35 : historique des décisions sur ce résultat (version relue, auteur, décision, date, commentaire)."""
    rows = desk.db.execute('SELECT version,user,decision,at,comment FROM integration_decisions5635 WHERE result=? ORDER BY id DESC LIMIT 50',
                           (ident,)).fetchall()
    if not rows:   # décisions antérieures à 5.6.35 : seule la dernière par version et par utilisateur a été conservée
        rows = desk.db.execute('SELECT version,user,decision,at,comment FROM integration_approvals5634 WHERE result=? ORDER BY at DESC LIMIT 50',
                               (ident,)).fetchall()
    return [dict(r) for r in rows]


def result(desk, ctx, ident):
    row = _own(desk, ctx, ident)
    if row['snapshot']:
        payload = json.loads(row['snapshot'])
        return {**_shape(ident, row['operation'], row['snapshot_version'], payload), 'decisions': _decisions(desk, ident)}
    if row['mission']:
        from . import integration5636
        if integration5636.instruction_status(desk, ctx, row)['state'] != 'succeeded':
            raise Refused('resultat_indisponible', '409 Conflict', retryable=True)
        produced = integration5636.instruction_payload(desk, ctx, row)
    else:
        job = desk.db.execute('SELECT status,result FROM jobs WHERE id=?', (row['job'],)).fetchone() if row['job'] else None
        if not job or job['status'] != 'done':
            raise Refused('resultat_indisponible', '409 Conflict', retryable=True)
        produced = json.loads(job['result'] or '{}') or {}
    if row['operation'] == 'fiche_dossier':
        from .dossier5627 import corrections, version as fiche_version
        number = produced.get('version')
        f = fiche_version(desk, row['matter'], number) if number else None
        if not f:
            raise Refused('version_produite_introuvable', '409 Conflict')
        payload = {'version': f['version'], 'data': f['data'], 'sources': f['sources'], 'corrections': corrections(desk, row['matter']),
                   'produit_le': f['created']}
    else:
        payload = produced
    version = _hash(payload)
    desk.db.execute("UPDATE integration_jobs5634 SET snapshot=?, snapshot_version=? WHERE id=? AND snapshot=''",
                    (json.dumps(payload, ensure_ascii=False, default=str), version, ident))
    desk.db.commit()
    stored = desk.db.execute('SELECT snapshot,snapshot_version FROM integration_jobs5634 WHERE id=?', (ident,)).fetchone()
    return {**_shape(ident, row['operation'], stored['snapshot_version'], json.loads(stored['snapshot'])), 'decisions': _decisions(desk, ident)}


def _shape(ident, operation, version, payload):
    return {'result_id': ident, 'version': version, 'operation': operation, 'content': payload,
            'validations_attendues': ['relecture par l’avocat'],
            'note': 'Résultat figé pour ce travail ; une décision ne vaut que pour cette version. La fiche courante du dossier peut avoir évolué depuis.'}


def approve(desk, ctx, ident, data):
    decision = data.get('decision')
    if decision not in DECISIONS:
        raise Refused('decision_invalide')
    comment = data.get('comment') or ''
    if not isinstance(comment, str) or len(comment) > 5000:
        raise Refused('commentaire_invalide')
    if decision == 'correction_demandee' and len(comment.strip()) < 3:
        raise Refused('correction_a_preciser')
    current = result(desk, ctx, ident)   # fige le résultat s'il ne l'était pas encore
    if not desk.db.in_transaction:
        desk.db.execute('BEGIN IMMEDIATE')
    try:
        stored = desk.db.execute('SELECT snapshot_version FROM integration_jobs5634 WHERE id=?', (ident,)).fetchone()
        if data.get('version') != stored['snapshot_version']:
            raise Refused('resultat_modifie_depuis_lecture', '409 Conflict')
        at = datetime.now(timezone.utc).isoformat()
        desk.db.execute('INSERT OR REPLACE INTO integration_approvals5634(result,version,user,decision,at,comment) VALUES(?,?,?,?,?,?)',
                        (ident, stored['snapshot_version'], ctx['user'], decision, at, comment.strip()))
        desk.db.execute('INSERT INTO integration_decisions5635(result,version,user,decision,at,comment) VALUES(?,?,?,?,?,?)',
                        (ident, stored['snapshot_version'], ctx['user'], decision, at, comment.strip()))   # historique complet, jamais réécrit
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.audit('integration5634_decision', {'result': ident, 'version': current['version'], 'decision': decision})
    return {'result_id': ident, 'version': current['version'], 'decision': decision}

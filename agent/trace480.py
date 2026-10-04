"""Journal de traçabilité 4.8.0 : ce que l'agent a lu, produit, et ce qui a été transmis à chaque appel de modèle.

Principes :
- le journal ne contient JAMAIS le texte des courriels, des pièces ou des requêtes : seulement des références
  (identifiants de sources, chemins, empreintes SHA-256, tailles), le modèle, le fournisseur et le caractère
  local ou externe de la destination ;
- l'enregistrement ne doit jamais empêcher le travail de l'agent (toute erreur d'écriture est ignorée) ;
- il est consultable par dossier, avec la réponse à « qu'a vu le modèle sur ce dossier, et quand ? », et exportable.
"""
import contextvars
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

from .common import Stop

CTX = contextvars.ContextVar('axiorhub_trace480', default=None)
DEFAULT_RETENTION_DAYS = 400
MAX_SOURCES = 60
MATTER_RE = re.compile(r'[A-Za-z0-9_.\-]{1,80}')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS trace_calls480(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, job_id TEXT NOT NULL DEFAULT '', job_kind TEXT NOT NULL DEFAULT '',
  matter TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT '', purpose TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '',
  external INTEGER NOT NULL DEFAULT 0, chars_sent INTEGER NOT NULL DEFAULT 0, sha256 TEXT NOT NULL DEFAULT '',
  fields TEXT NOT NULL DEFAULT '[]', sources TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'ok', error TEXT NOT NULL DEFAULT '', duration_ms INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS trace_calls480_matter ON trace_calls480(matter, at);
CREATE TABLE IF NOT EXISTS trace_events480(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL,
  what TEXT NOT NULL, why TEXT NOT NULL DEFAULT '', refs TEXT NOT NULL DEFAULT '{}', job_id TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS trace_events480_matter ON trace_events480(matter, at);
'''


def _now():
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(db):
    db.executescript(SCHEMA)
    db.commit()


# ------------------------------------------------------------------ contexte d'exécution
def begin(job_id='', job_kind='', matter=''):
    """Ouvre le contexte d'un traitement (appelé par le worker). Remplace le contexte précédent."""
    CTX.set({'job_id': str(job_id or ''), 'job_kind': str(job_kind or '')[:60],
             'matter': _clean_matter(matter), 'notes': {}})


def end():
    CTX.set(None)


def _clean_matter(value):
    value = str(value or '').strip()
    return value if MATTER_RE.fullmatch(value) else ''


def set_matter(matter):
    ctx = CTX.get()
    if ctx is None:
        ctx = {'job_id': '', 'job_kind': '', 'matter': '', 'notes': {}}
        CTX.set(ctx)
    ctx['matter'] = _clean_matter(matter)
    ctx['notes'] = {}


def note(**values):
    """Ajoute des éléments (règles appliquées, profil de ton, niveau d'autonomie) au prochain appel de modèle."""
    ctx = CTX.get()
    if ctx is None:
        ctx = {'job_id': '', 'job_kind': '', 'matter': '', 'notes': {}}
        CTX.set(ctx)
    ctx['notes'].update({k: v for k, v in values.items()})


# ------------------------------------------------------------------ appels de modèle
def _sources_of(data):
    """Références des sources d'une requête, sans leur contenu."""
    found = []
    if not isinstance(data, dict):
        return found
    rows = data.get('sources')
    if isinstance(rows, list):
        for item in rows[:MAX_SOURCES]:
            if not isinstance(item, dict):
                continue
            label = str(item.get('path') or item.get('filename') or item.get('subject') or item.get('summary') or '')[:300]
            content = item.get('text') or item.get('content') or item.get('excerpt') or ''
            found.append({'id': str(item.get('id', ''))[:120], 'kind': str(item.get('kind', ''))[:40], 'label': label,
                          'sha256': hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16] if content else ''})
    incoming = data.get('incoming')
    if isinstance(incoming, dict):
        found.insert(0, {'id': 'incoming', 'kind': 'email_received', 'label': str(incoming.get('subject', ''))[:200],
                         'sha256': hashlib.sha256(json.dumps(incoming, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]})
    return found


def _matter_of(data):
    if not isinstance(data, dict):
        return ''
    for key in ('matter', 'dossier'):
        value = data.get(key)
        if isinstance(value, dict):
            value = value.get('id')
        if isinstance(value, str) and _clean_matter(value):
            return value
    value = data.get('matter_id')
    return _clean_matter(value) if isinstance(value, str) else ''


def record_call(cfg, stage, data, messages, status='ok', error='', duration_ms=0):
    """Enregistre un appel de modèle. Ne lève jamais d'exception."""
    try:
        state = str((cfg or {}).get('state_dir') or '')
        if not state:
            return
        provider_type = str(cfg.get('provider_type') or cfg.get('type') or 'ollama')
        external = provider_type != 'ollama'
        try:
            destination = (urlparse(str(cfg.get('url') or '')).hostname or '') if external else 'local'
        except ValueError:
            destination = ''
        ctx = CTX.get() or {}
        matter = ctx.get('matter') or _matter_of(data)
        payload = json.dumps(messages, ensure_ascii=False, default=str)
        fields = sorted(data.keys())[:40] if isinstance(data, dict) else []
        db = sqlite3.connect(Path(state) / 'desk.sqlite3', timeout=5)
        try:
            ensure_schema(db)
            db.execute('''INSERT INTO trace_calls480(at,job_id,job_kind,matter,stage,purpose,provider,model,destination,external,
              chars_sent,sha256,fields,sources,notes,status,error,duration_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (_now(), ctx.get('job_id', ''), ctx.get('job_kind', ''), matter, str(stage or '')[:60],
                        str(cfg.get('purpose') or '')[:60], str(cfg.get('provider_id') or provider_type)[:60],
                        str(cfg.get('model') or '')[:120], destination[:200], 1 if external else 0, len(payload),
                        hashlib.sha256(payload.encode('utf-8', 'replace')).hexdigest(),
                        json.dumps(fields), json.dumps(_sources_of(data), ensure_ascii=False),
                        json.dumps(ctx.get('notes') or {}, ensure_ascii=False, default=str)[:4000],
                        status, str(error or '')[:120], int(duration_ms)))
            db.commit()
        finally:
            db.close()
    except Exception:
        return


def event(desk, matter, kind, what, why='', refs=None):
    """Événement d'activité (production, action, proposition, règle appliquée). Ne lève jamais d'exception."""
    try:
        ctx = CTX.get() or {}
        ensure_schema(desk.db)
        desk.db.execute('INSERT INTO trace_events480(at,matter,kind,what,why,refs,job_id) VALUES(?,?,?,?,?,?,?)',
                        (_now(), _clean_matter(matter) or ctx.get('matter', ''), str(kind)[:40], str(what)[:300],
                         str(why)[:600], json.dumps(refs or {}, ensure_ascii=False, default=str)[:3000], ctx.get('job_id', '')))
        desk.db.commit()
    except Exception:
        return


# ------------------------------------------------------------------ consultation
def _rows(desk, sql, params):
    ensure_schema(desk.db)
    return [dict(r) for r in desk.db.execute(sql, params)]


def _window(since, until):
    since = str(since or '')
    until = str(until or '')
    for value in (since, until):
        if value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}(T[\d:.+\-Z]+)?', value):
            raise Stop('periode_invalide')
    if until and len(until) == 10:
        until += 'T23:59:59.999999+00:00'
    return since, until


def matter_view(desk, matter, since='', until='', limit=500):
    matter = _clean_matter(matter)
    if not matter:
        raise Stop('dossier_invalide')
    since, until = _window(since, until)
    limit = max(1, min(int(limit), 2000))
    where, params = 'matter=?', [matter]
    if since:
        where += ' AND at>=?'
        params.append(since)
    if until:
        where += ' AND at<=?'
        params.append(until)
    calls = _rows(desk, 'SELECT * FROM trace_calls480 WHERE ' + where + ' ORDER BY at DESC, id DESC LIMIT ?', params + [limit])
    for call in calls:
        for key in ('fields', 'sources', 'notes'):
            call[key] = json.loads(call[key] or ('{}' if key == 'notes' else '[]'))
    events = _rows(desk, 'SELECT * FROM trace_events480 WHERE ' + where + ' ORDER BY at DESC, id DESC LIMIT ?', params + [limit])
    for item in events:
        item['refs'] = json.loads(item['refs'] or '{}')
    seen = {}
    for call in reversed(calls):
        for source in call['sources']:
            key = (source['kind'], source['id'], source['label'])
            row = seen.setdefault(key, {'kind': source['kind'], 'id': source['id'], 'label': source['label'],
                                        'first_seen': call['at'], 'last_seen': call['at'], 'calls': 0,
                                        'external_calls': 0, 'destinations': set(), 'models': set()})
            row['last_seen'] = call['at']
            row['calls'] += 1
            row['external_calls'] += call['external']
            row['destinations'].add(call['destination'] or 'local')
            row['models'].add(call['model'])
    saw = sorted(({**v, 'destinations': sorted(v['destinations']), 'models': sorted(v['models'])} for v in seen.values()),
                 key=lambda x: x['last_seen'], reverse=True)
    summary = {'calls': len(calls), 'external_calls': sum(c['external'] for c in calls),
               'errors': sum(1 for c in calls if c['status'] != 'ok'),
               'first_call': calls[-1]['at'] if calls else '', 'last_call': calls[0]['at'] if calls else '',
               'distinct_sources': len(saw),
               'destinations': sorted({c['destination'] or 'local' for c in calls}),
               'truncated': len(calls) >= limit}
    return {'matter': matter, 'since': since, 'until': until, 'summary': summary, 'model_saw': saw,
            'calls': calls, 'events': events,
            'note': 'Le journal ne conserve ni le texte des courriels et pièces, ni les requêtes : uniquement leurs références et empreintes.'}


def matters_with_activity(desk):
    return _rows(desk, '''SELECT matter, COUNT(*) calls, MAX(at) last FROM trace_calls480 WHERE matter<>'' GROUP BY matter ORDER BY last DESC LIMIT 200''', ())


def export(desk, matter, fmt='json', since='', until=''):
    view = matter_view(desk, matter, since, until, 2000)
    stamp = _now()
    if fmt == 'csv':
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=';', lineterminator='\n')
        writer.writerow(['horodatage', 'type', 'étape_ou_événement', 'modèle', 'fournisseur', 'destination', 'externe',
                         'caractères_transmis', 'empreinte_sha256', 'sources_ou_motif', 'état'])
        for call in view['calls']:
            writer.writerow([call['at'], 'appel_modèle', call['stage'], call['model'], call['provider'], call['destination'],
                             'oui' if call['external'] else 'non', call['chars_sent'], call['sha256'],
                             ' | '.join('%s:%s' % (s['kind'], s['label'] or s['id']) for s in call['sources']), call['status']])
        for item in view['events']:
            writer.writerow([item['at'], 'événement', item['kind'] + ' : ' + item['what'], '', '', '', '', '', '', item['why'], 'ok'])
        content = buf.getvalue()
        name = 'tracabilite-%s.csv' % view['matter']
    elif fmt == 'json':
        content = json.dumps({'exporte_le': stamp, **view}, ensure_ascii=False, indent=2, default=str)
        name = 'tracabilite-%s.json' % view['matter']
    else:
        raise Stop('format_export_invalide')
    digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
    desk.audit('tracabilite_480_export', {'matter': view['matter'], 'format': fmt, 'calls': view['summary']['calls'], 'sha256': digest})
    return {'filename': name, 'content': content, 'sha256': digest, 'format': fmt, 'exported_at': stamp,
            'calls': view['summary']['calls'], 'events': len(view['events'])}


def overview(desk, days=30):
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days), 3650)))).isoformat()
    rows = _rows(desk, '''SELECT model, provider, destination, external, COUNT(*) calls, SUM(chars_sent) chars, MAX(at) last
      FROM trace_calls480 WHERE at>=? GROUP BY model, provider, destination, external ORDER BY calls DESC''', (since,))
    return {'days': days, 'by_model': rows, 'external_calls': sum(r['calls'] for r in rows if r['external']),
            'total_calls': sum(r['calls'] for r in rows)}


def purge(desk, days=None):
    days = int(days if days is not None else desk.settings('trace480:retention_days', DEFAULT_RETENTION_DAYS))
    days = max(30, min(days, 3650))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    ensure_schema(desk.db)
    a = desk.db.execute('DELETE FROM trace_calls480 WHERE at<?', (cutoff,)).rowcount
    b = desk.db.execute('DELETE FROM trace_events480 WHERE at<?', (cutoff,)).rowcount
    desk.db.commit()
    return {'deleted_calls': a, 'deleted_events': b, 'retention_days': days}

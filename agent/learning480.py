"""Tableau d'apprentissage 4.8.0.

Mesure, mois par mois et par type de courriel, la part des brouillons de l'agent envoyés tels quels, légèrement
corrigés ou réécrits. Le calcul ne porte QUE sur les courriels réellement envoyés (dossier Envoyés) qui ont pu être
rapprochés avec certitude d'un brouillon de l'agent : aucun autre contenu n'est lu, et les envois sans brouillon
ne sont que comptés. Seuls des compteurs, des catégories et des formules de politesse d'une liste fermée sont
conservés ; aucun texte, nom ou fait.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from .common import Stop
from . import style480, tone480

KINDS = {'appointment': 'Rendez-vous', 'status': 'Point sur le dossier', 'documents': 'Pièces et documents',
         'administrative': 'Administratif', 'clarification': 'Demande de précisions', 'inconnu': 'Type non déterminé'}
ROLE_LABELS = {'client': 'Client', 'confrere': 'Confrère', 'greffe': 'Greffe', 'adversaire': 'Adversaire',
               'administration': 'Administration', 'autre': 'Autre / non déterminé'}
OUTCOME_LABELS = {'tel_quel': 'Envoyés tels quels', 'leger': 'Légèrement corrigés', 'reecrit': 'Réécrits'}
MIN_SAMPLE = 5

SCHEMA = '''
CREATE TABLE IF NOT EXISTS outcomes480(
  key TEXT PRIMARY KEY, sent_at TEXT NOT NULL, month TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
  intent TEXT NOT NULL DEFAULT 'inconnu', outcome TEXT NOT NULL, similarity REAL NOT NULL,
  words_draft INTEGER NOT NULL, words_final INTEGER NOT NULL, opening TEXT NOT NULL DEFAULT '',
  closing TEXT NOT NULL DEFAULT '', changes TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL DEFAULT 'envoi',
  created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS outcomes480_month ON outcomes480(month);
'''


def ensure_schema_db(db):
    db.executescript(SCHEMA)
    db.commit()


def ensure_schema(desk):
    ensure_schema_db(desk.db)
    tone480.ensure_schema(desk)


def _role_of(audience_json):
    """Rôle de ton déduit des liaisons adresse/rôle de l'exemple envoyé (sans accès au contenu)."""
    try:
        bindings = json.loads(audience_json or '[]')
    except (ValueError, TypeError):
        return ''
    roles = set()
    for pair in bindings:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            email, matter_role = str(pair[0]).lower(), str(pair[1])
            if tone480.INSTITUTION_RE.search(email):
                roles.add('greffe')
            elif tone480.ADMIN_RE.search(email):
                roles.add('administration')
            else:
                roles.add(tone480.ROLE_MAP.get(matter_role, 'adversaire' if matter_role == 'tiers' else ''))
    roles.discard('')
    if not roles:
        return ''
    return max(roles, key=lambda r: tone480.FORMALITY_RANK[r])


def compute(draft, final):
    outcome, similarity = style480.classify(draft, final)
    opening, closing, words_final = style480.final_formulas(final)
    return {'outcome': outcome, 'similarity': similarity, 'words_draft': len(style480._words(style480.core(draft))),
            'words_final': words_final, 'opening': opening, 'closing': closing,
            'changes': [] if outcome == 'tel_quel' else style480.changes(draft, final)}


def record_match(cfg, key, sent_at, audience_json, intent, draft, final, source='envoi'):
    """Appelé par la mémoire des envoyés quand un envoi est rapproché d'un brouillon. Ne lève jamais d'exception."""
    try:
        state = str((cfg or {}).get('state_dir') or '')
        if not state or not draft or not final:
            return False
        data = compute(draft, final)
        month = str(sent_at)[:7]
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
            return False
        db = sqlite3.connect(Path(state) / 'desk.sqlite3', timeout=5)
        try:
            ensure_schema_db(db)
            db.execute('''INSERT OR IGNORE INTO outcomes480(key,sent_at,month,role,intent,outcome,similarity,words_draft,words_final,
              opening,closing,changes,source,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (key, str(sent_at), month, _role_of(audience_json), intent if intent in KINDS else 'inconnu',
                        data['outcome'], data['similarity'], data['words_draft'], data['words_final'], data['opening'],
                        data['closing'], json.dumps(data['changes'], ensure_ascii=False), source,
                        datetime.now(timezone.utc).isoformat()))
            db.commit()
        finally:
            db.close()
        return True
    except Exception:
        return False


def forget(cfg, key):
    try:
        db = sqlite3.connect(Path(cfg['state_dir']) / 'desk.sqlite3', timeout=5)
        try:
            ensure_schema_db(db)
            db.execute('DELETE FROM outcomes480 WHERE key=?', (key,))
            db.commit()
        finally:
            db.close()
    except Exception:
        return


def _sent_db(cfg):
    path = Path(cfg['state_dir']) / 'sent-memory.sqlite3'
    if not path.is_file():
        return None
    db = sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def backfill(desk):
    """Rattrape les envois rapprochés avant la 4.8.0 (encore présents dans la mémoire des envoyés)."""
    ensure_schema(desk)
    db = _sent_db(desk.c)
    if db is None:
        return {'added': 0}
    added = 0
    try:
        known = {r[0] for r in desk.db.execute('SELECT key FROM outcomes480')}
        for row in db.execute("SELECT key,sent_at,body,draft,audience FROM examples_v2 WHERE provenance='matched_draft_and_sent' AND draft<>''"):
            if row['key'] in known:
                continue
            if record_match(desk.c, row['key'], row['sent_at'], row['audience'], 'inconnu', row['draft'], row['body'], 'rattrapage'):
                added += 1
    except sqlite3.Error:
        pass
    finally:
        db.close()
    return {'added': added}


def unmatched_count(desk):
    """Envois enregistrés mais sans brouillon rapproché : comptés, jamais lus."""
    db = _sent_db(desk.c)
    if db is None:
        return 0
    try:
        return int(db.execute("SELECT COUNT(*) FROM examples_v2 WHERE provenance<>'matched_draft_and_sent'").fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        db.close()


def _pct(part, total):
    return round(100 * part / total, 1) if total else None


def _bucket(rows):
    total = len(rows)
    counts = {k: sum(1 for r in rows if r['outcome'] == k) for k in OUTCOME_LABELS}
    return {'total': total, **counts, 'pct': {k: _pct(v, total) for k, v in counts.items()},
            'low_sample': total < MIN_SAMPLE}


def dashboard(desk, months=12, kind='', role=''):
    ensure_schema(desk)
    months = max(1, min(int(months), 36))
    where, params = [], []
    if kind:
        if kind not in KINDS:
            raise Stop('filtre_invalide')
        where.append('intent=?')
        params.append(kind)
    if role:
        if role not in ROLE_LABELS:
            raise Stop('filtre_invalide')
        where.append("role=?" if role != 'autre' else "role=''")
        if role != 'autre':
            params.append(role)
    sql = 'SELECT * FROM outcomes480' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY month'
    rows = [dict(r) for r in desk.db.execute(sql, params)]
    month_keys = sorted({r['month'] for r in rows})[-months:]
    rows = [r for r in rows if r['month'] in month_keys]
    by_month = [{'month': m, **_bucket([r for r in rows if r['month'] == m])} for m in month_keys]
    by_kind = []
    for k, label in KINDS.items():
        part = [r for r in rows if r['intent'] == k]
        if part:
            by_kind.append({'kind': k, 'label': label, **_bucket(part)})
    by_role = []
    for k, label in ROLE_LABELS.items():
        part = [r for r in rows if (r['role'] or 'autre') == k]
        if part:
            by_role.append({'role': k, 'label': label, **_bucket(part)})
    # modifications les plus fréquentes
    counter = {}
    modified = [r for r in rows if r['outcome'] != 'tel_quel']
    for r in modified:
        seen = set()
        for c in json.loads(r['changes'] or '[]'):
            key = (c['cat'], c['from'], c['to'])
            if key in seen:
                continue
            seen.add(key)
            counter[key] = counter.get(key, 0) + 1
    top = [{'cat': c, 'label': style480.LABELS.get(c, c), 'from': f, 'to': t, 'count': n,
            'share_pct': _pct(n, len(modified))} for (c, f, t), n in sorted(counter.items(), key=lambda kv: -kv[1])[:12]]
    # tendance : trois derniers mois contre les trois précédents
    recent = [r for r in rows if r['month'] in month_keys[-3:]]
    previous = [r for r in rows if r['month'] in month_keys[-6:-3]]
    trend = {'recent': _pct(sum(r['outcome'] == 'tel_quel' for r in recent), len(recent)),
             'previous': _pct(sum(r['outcome'] == 'tel_quel' for r in previous), len(previous)),
             'recent_n': len(recent), 'previous_n': len(previous), 'direction': 'insuffisant'}
    if len(recent) >= MIN_SAMPLE and len(previous) >= MIN_SAMPLE:
        gap = trend['recent'] - trend['previous']
        trend['direction'] = 'hausse' if gap >= 5 else 'baisse' if gap <= -5 else 'stable'
    return {'months': by_month, 'total': _bucket(rows), 'by_kind': by_kind, 'by_role': by_role, 'top_changes': top,
            'trend': trend, 'unmatched': unmatched_count(desk), 'min_sample': MIN_SAMPLE,
            'filters': {'kind': kind, 'role': role, 'months': months},
            'method': ('Calcul sur les courriels envoyés rapprochés avec certitude d’un brouillon de l’agent. '
                       '« Tel quel » : même texte une fois la signature et la mise en forme ignorées ; '
                       '« léger » : au moins 80 % du texte conservé ; « réécrit » : moins de 80 %.')}

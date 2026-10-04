"""Échéances de procédure (AxiorHub 4.5.0).

Le modèle de langage ne fixe jamais une date. Ce module :
* repère, avec des règles déterministes, l'événement de départ (signification, notification,
  déclaration d'appel, avis...) et sa date, citée telle qu'elle figure dans la pièce ;
* demande le calcul au moteur ``deadlines450`` ;
* tient l'agenda à jour sans doublon (identifiant d'événement stable) ;
* contrôle la cohérence agenda / dossier / actes préparés et émet des rappels en cascade ;
* journalise toute correction manuelle.
"""
from .common import matter_display
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import PurePosixPath
import re
from zoneinfo import ZoneInfo

from . import deadlines450 as engine
from .common import Stop, digest, load_matters
from .notices440 import DATE_NUM, DATE_TEXT, MONTHS, NOTICE_EXTENSIONS, fold_chars

ACTIVE = ('a_confirmer', 'confirmee', 'manuel')
STATUS_LABELS = {'a_confirmer': 'À confirmer', 'confirmee': 'Confirmée', 'manuel': 'Corrigée à la main',
                 'a_completer': 'À compléter', 'terminee': 'Terminée', 'annulee': 'Annulée'}
OFFSETS = (30, 14, 7, 2, 0)
OFFSET_LABELS = {30: 'J-30', 14: 'J-14', 7: 'J-7', 2: 'J-2', 0: 'jour J'}
DEFAULT_RULE = {
    'signification_jugement': 'appel_jugement_contentieux',
    'signification_ordonnance_refere': 'appel_ordonnance_refere',
    'declaration_appel': 'conclusions_appelant',
    'signification_declaration_appel': 'constitution_intime',
    'avis_greffe_circuit': 'signification_da_appelant',
    'avis_fixation_bref_delai': 'bref_delai_appelant',
    'conclusions_appelant': 'conclusions_intime',
    'signification_decision_cassation': 'pourvoi_cassation',
}
# (événement, motif des indices, indice obligatoire du contexte)
CUES = (
    ('signification_declaration_appel', r"signification\s+(?:de\s+la\s+)?declaration\s+d.appel|declaration\s+d.appel\s+(?:a\s+ete\s+)?signifiee?", ''),
    ('avis_fixation_bref_delai', r"avis\s+de\s+fixation", r'bref\s+delai|905'),
    ('avis_greffe_circuit', r"avis\s+(?:du\s+greffe|de\s+circuit|d.orientation)|article\s+902", ''),
    ('declaration_appel', r"declaration\s+d.appel", ''),
    ('conclusions_appelant', r"conclusions?\s+(?:de\s+l.appelant|d.appelant)?", r'appelant'),
    ('signification_ordonnance_refere', r"ordonnance\s+de\s+refere", r'signifi|notifi'),
    ('signification_jugement', r"(?:jugement|decision|arret)", r'signifi|notifi'),
)
ACTION = re.compile(r"(?:signifie[es]?\s+(?:a\s+\w+\s+)?|notifie[es]?\s+|signification\s+(?:du\s+\w+\s+)?|notification\s+|"
                    r"en\s+date\s+du\s+|date\s+du\s+|\bdu\s+|\ble\s+|\bremise\s+le\s+|\bformee?\s+le\s+)")
CANDIDATE_NAME = re.compile(r"signif|jugement|ordonnance|declaration d.appel|avis|conclusions|arret|huissier|commissaire de justice|notification")


# --------------------------------------------------------------- storage
def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS deadlines450(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, rule_id TEXT NOT NULL, start_event TEXT NOT NULL,
      start_date TEXT NOT NULL, regime_date TEXT NOT NULL DEFAULT '', distance TEXT NOT NULL DEFAULT 'none',
      alsace INTEGER NOT NULL DEFAULT 0, due TEXT NOT NULL DEFAULT '', calc TEXT NOT NULL DEFAULT '{}',
      error TEXT NOT NULL DEFAULT '', source_path TEXT NOT NULL DEFAULT '', source_excerpt TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL, origin TEXT NOT NULL, calendar_uid TEXT NOT NULL DEFAULT '',
      calendar_state TEXT NOT NULL DEFAULT '', act_prepared INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
      created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS deadlines450_matter ON deadlines450(matter,status);
    CREATE INDEX IF NOT EXISTS deadlines450_due ON deadlines450(due);
    CREATE TABLE IF NOT EXISTS deadline_journal450(
      id INTEGER PRIMARY KEY AUTOINCREMENT, deadline_id TEXT NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL,
      reason TEXT NOT NULL DEFAULT '', before TEXT NOT NULL DEFAULT '{}', after TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS deadline_reminders450(
      deadline_id TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(deadline_id,kind));
    CREATE TABLE IF NOT EXISTS deadline_obs450(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, path TEXT NOT NULL, etag TEXT NOT NULL, status TEXT NOT NULL,
      result TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL, updated TEXT NOT NULL);
    ''')
    desk.db.commit()


def enabled(desk):
    return bool(desk.settings('automation:deadlines450_enabled', True))


def _matter(desk, matter_id):
    return next((m for m in load_matters(desk.c) if m['id'] == matter_id), None)


def matter_label(desk, matter_id):
    m = _matter(desk, matter_id)
    return matter_display(m) if m else matter_id


def _row(desk, ident):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM deadlines450 WHERE id=?', (ident,)).fetchone()
    return dict(row) if row else None


def public(row):
    out = dict(row)
    try:
        out['calc'] = json.loads(out.get('calc') or '{}')
    except ValueError:
        out['calc'] = {}
    out['status_label'] = STATUS_LABELS.get(out['status'], out['status'])
    try:
        out['rule_label'] = engine.rule(out['rule_id'])['label']
    except engine.DeadlineError:
        out['rule_label'] = out['rule_id']
    out['start_event_label'] = engine.start_event_label(out['start_event'])
    if out.get('due'):
        out['due_label'] = engine.fr_date(date.fromisoformat(out['due']))
    return out


# ------------------------------------------------------------ extraction
def _dates_after(folded, pos, span=110):
    """Dates avec année explicite situées juste après ``pos`` (jamais d'année devinée)."""
    zone = folded[pos:pos + span]
    found = []
    for match in DATE_TEXT.finditer(zone):
        if not match[3]:
            continue
        try:
            found.append((match.start(), date(int(match[3]), MONTHS[match[2]], int(match[1])), match.end()))
        except ValueError:
            continue
    for match in DATE_NUM.finditer(zone):
        year = int(match[3])
        year += 2000 if year < 100 else 0
        try:
            found.append((match.start(), date(year, int(match[2]), int(match[1])), match.end()))
        except ValueError:
            continue
    return sorted(found)


def extract_start_events(text, today=None):
    """Événements de départ trouvés dans ``text``. Chaque date figure littéralement dans le texte."""
    today = today or date.today()
    folded = fold_chars(text)
    out, seen = [], set()
    for event, cue_re, need in CUES:
        for cue in re.finditer(cue_re, folded):
            sentence_start = max(folded.rfind('. ', 0, cue.start()), folded.rfind('\n\n', 0, cue.start()), 0)
            ending = [i for i in (folded.find('. ', cue.end()), folded.find('\n\n', cue.end())) if i != -1]
            sentence_end = min(ending) if ending else len(folded)
            window = folded[sentence_start:min(sentence_end, cue.end() + 140)]
            if need and not re.search(need, window):
                continue
            if event == 'signification_jugement' and not re.search(r'signifi|notifi', window[cue.start() - sentence_start:]):
                continue
            if event == 'declaration_appel' and re.search(r'signifi', window):
                continue
            after = [d for d in _dates_after(folded, cue.end()) if d[1] <= today + timedelta(days=2) and d[1].year >= 2000]
            chosen = None
            for offset, when, end in after:
                lead = folded[cue.end():cue.end() + offset]
                # la date visée suit l'indice (« signifié le ... », « en date du ... ») : une autre date
                # d'abord citée (« jugement du 5 mars, signifié le 12 mars ») ne passe que si l'action la précède.
                if event in ('signification_jugement', 'signification_ordonnance_refere') and not re.search(r'signifi|notifi|signification|notification', folded[cue.start():cue.end() + offset + 1]):
                    continue
                chosen = (when, cue.end() + offset, cue.end() + end)
                break
            if not chosen:
                continue
            key = (event, chosen[0])
            if key in seen:
                continue
            seen.add(key)
            context = re.sub(r'\s+', ' ', text[max(0, cue.start() - 60):chosen[2] + 40]).strip()
            out.append({'event': event, 'date': chosen[0], 'context': context[:300],
                        'rule_id': DEFAULT_RULE.get(event, ''),
                        'alternatives': [r['id'] for r in engine.rules() if r['start_event'] == event]})
    return sorted(out, key=lambda e: (e['date'], e['event']))


def looks_like_candidate(path):
    p = PurePosixPath(path)
    return p.suffix.lower() in NOTICE_EXTENSIONS and bool(CANDIDATE_NAME.search(fold_chars(p.stem)))


# ------------------------------------------------------------- creation
def _journal(desk, ident, action, reason='', before=None, after=None):
    desk.db.execute('INSERT INTO deadline_journal450(deadline_id,at,action,reason,before,after) VALUES(?,?,?,?,?,?)',
                    (ident, desk.now(), action, reason, json.dumps(before or {}, ensure_ascii=False, default=str),
                     json.dumps(after or {}, ensure_ascii=False, default=str)))


def regime_for(desk, matter_id):
    ensure_schema(desk)
    row = desk.db.execute("SELECT start_date FROM deadlines450 WHERE matter=? AND start_event='declaration_appel' "
                          "AND status NOT IN ('annulee') ORDER BY start_date DESC LIMIT 1", (matter_id,)).fetchone()
    return row[0] if row else ''


def _compute_fields(rule_id, start, distance, alsace, regime_date):
    try:
        result = engine.compute(rule_id, start, distance=distance, alsace=alsace, regime_date=regime_date or None)
        return result['due'], json.dumps(result, ensure_ascii=False), ''
    except engine.DeadlineError as ex:
        return '', '{}', ex.message


def create_deadline(desk, matter_id, rule_id, start, distance='none', alsace=False, regime_date='', source_path='',
                    excerpt='', origin='auto', status='a_confirmer', note=''):
    """Crée (ou retrouve) l'échéance. Idempotent : même dossier, règle, départ et régime = même ligne."""
    ensure_schema(desk)
    if not _matter(desk, matter_id):
        raise Stop('dossier_absent')
    spec = engine.rule(rule_id)
    start = str(start)
    date.fromisoformat(start)
    if spec.get('regime') and not regime_date:
        regime_date = regime_for(desk, matter_id)
        if not regime_date and spec['start_event'] == 'declaration_appel':
            regime_date = start
    ident = digest('deadline450|%s|%s|%s' % (matter_id, rule_id, start))
    existing = _row(desk, ident)
    if existing:
        return public(existing) | {'created_now': False}
    due, calc, error = _compute_fields(rule_id, start, distance, alsace, regime_date)
    if error:
        status = 'a_completer'
    now = desk.now()
    desk.db.execute('''INSERT INTO deadlines450(id,matter,rule_id,start_event,start_date,regime_date,distance,alsace,due,calc,
        error,source_path,source_excerpt,status,origin,calendar_uid,calendar_state,act_prepared,note,created,updated)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (ident, matter_id, rule_id, spec['start_event'], start, regime_date or '', distance, int(bool(alsace)), due, calc,
                     error, source_path[:500], excerpt[:400], status, origin, '', '', 0, note[:500], now, now))
    _journal(desk, ident, 'creation', origin, {}, {'rule': rule_id, 'start': start, 'due': due, 'status': status})
    desk.db.commit()
    desk.audit('echeance_450_creee', {'matter': matter_id, 'rule': rule_id, 'origin': origin, 'due': due})
    return public(_row(desk, ident)) | {'created_now': True}


# --------------------------------------------------------------- agenda
def _calendar_urls(desk):
    cfg = desk.c.get('calendar', {})
    return cfg.get('urls', []), cfg.get('timezone', 'Europe/Paris')


def _title(desk, row):
    prefix = {'a_confirmer': 'Échéance À CONFIRMER – ', 'terminee': 'TERMINÉE – ', 'annulee': 'ANNULÉE – '}.get(row['status'], 'Échéance – ')
    try:
        label = engine.rule(row['rule_id'])['label']
    except engine.DeadlineError:
        label = row['rule_id']
    return (prefix + label + ' – ' + matter_label(desk, row['matter']))[:200]


def _description(row):
    calc = json.loads(row.get('calc') or '{}')
    lines = ['Créée par AxiorHub (moteur de délais, aucune date déduite par un modèle).']
    lines += calc.get('steps', [])
    if row.get('origin') == 'manuel' or row.get('status') == 'manuel':
        lines.append('Date corrigée à la main (voir le journal).')
    if row.get('source_path'):
        lines.append('Source : ' + row['source_path'])
    return '\n'.join(lines)[:1900]


def sync_calendar(desk, dav, ident):
    """Crée ou met à jour l'unique événement de l'échéance (UID stable : pas de doublon)."""
    row = _row(desk, ident)
    if not row or not row['due']:
        return ''
    urls, tz_name = _calendar_urls(desk)
    if not urls or not desk.settings('automation:deadline_calendar450', True):
        state = 'non_configure'
    else:
        tz = ZoneInfo(tz_name)
        when = date.fromisoformat(row['due'])
        start = datetime(when.year, when.month, when.day, 9, 0, tzinfo=tz)
        title, description = _title(desk, row), _description(row)
        state = ''
        from . import autonomy480
        if not autonomy480.allows(desk, 'agenda_echeance', 'agir'):
            autonomy480.pending_add(desk, 'agenda_echeance', row['matter'] if 'matter' in row.keys() else '', title,
                {'uid': ident, 'title': title, 'start': start.isoformat(), 'end': (start + timedelta(minutes=30)).isoformat(),
                 'description': description, 'deadline_id': ident})
            desk.db.execute('UPDATE deadlines450 SET calendar_state=?,updated=? WHERE id=?', ('a_valider', desk.now(), ident))
            desk.db.commit()
            return 'a_valider'
        for replace in ((True, False) if row['calendar_uid'] else (False, True)):
            try:
                uid = dav.put_event(urls[0], ident, title, start, start + timedelta(minutes=30), description, replace=replace)
                state = 'mis_a_jour' if replace else 'cree'
                desk.db.execute('UPDATE deadlines450 SET calendar_uid=? WHERE id=?', (uid, ident))
                break
            except Stop as ex:
                if str(ex) != 'http_412' and str(ex) != 'http_404':
                    state = 'erreur:' + str(ex)[:60]
                    break
                state = 'erreur:' + str(ex)
    desk.db.execute('UPDATE deadlines450 SET calendar_state=?,updated=? WHERE id=?', (state, desk.now(), ident))
    desk.db.commit()
    return state


# -------------------------------------------------------- user actions
def _need_reason(reason):
    reason = re.sub(r'\s+', ' ', str(reason or '')).strip()
    if len(reason) < 5:
        raise Stop('motif_obligatoire')
    return reason[:300]


def confirm(desk, dav, ident, rule_id='', distance=None, alsace=None, regime_date=None):
    row = _row(desk, ident)
    if not row:
        raise Stop('echeance_absente')
    if row['status'] in ('terminee', 'annulee'):
        raise Stop('echeance_close')
    before = {'rule': row['rule_id'], 'due': row['due'], 'status': row['status']}
    rule_id = rule_id or row['rule_id']
    spec = engine.rule(rule_id)
    if spec['start_event'] != row['start_event']:
        raise Stop('regle_incompatible_avec_evenement')
    distance = row['distance'] if distance is None else distance
    alsace = bool(row['alsace']) if alsace is None else bool(alsace)
    regime_date = row['regime_date'] if regime_date is None else regime_date
    keep_manual = (row['status'] == 'manuel' and rule_id == row['rule_id'] and distance == row['distance']
                   and alsace == bool(row['alsace']) and (regime_date or '') == row['regime_date'])
    if keep_manual:   # une date corrigée à la main reste telle quelle jusqu'à nouvelle correction
        due, calc, error, status = row['due'], row['calc'], '', 'manuel'
    else:
        due, calc, error = _compute_fields(rule_id, row['start_date'], distance, alsace, regime_date)
        status = 'a_completer' if error else 'confirmee'
    desk.db.execute('UPDATE deadlines450 SET rule_id=?,distance=?,alsace=?,regime_date=?,due=?,calc=?,error=?,status=?,updated=? WHERE id=?',
                    (rule_id, distance, int(alsace), regime_date or '', due, calc, error, status, desk.now(), ident))
    _journal(desk, ident, 'confirmation', '', before, {'rule': rule_id, 'due': due, 'status': status})
    desk.db.commit()
    state = sync_calendar(desk, dav, ident) if due else ''
    desk.audit('echeance_450_confirmee', {'id': ident[:12], 'due': due})
    return public(_row(desk, ident)) | {'calendar': state}


def correct(desk, dav, ident, reason, new_due='', start_date='', rule_id='', distance=None, regime_date=None):
    """Correction manuelle, toujours motivée et journalisée ; l'événement d'agenda est remplacé, jamais dupliqué."""
    reason = _need_reason(reason)
    row = _row(desk, ident)
    if not row:
        raise Stop('echeance_absente')
    before = {'rule': row['rule_id'], 'start': row['start_date'], 'due': row['due'], 'status': row['status']}
    if new_due:
        try:
            forced = date.fromisoformat(str(new_due))
        except ValueError:
            raise Stop('date_invalide') from None
        calc = json.loads(row['calc'] or '{}')
        calc.setdefault('steps', []).append('Date corrigée à la main le %s : %s (motif : %s).' % (
            desk.now()[:10], engine.fr_date(forced), reason))
        desk.db.execute("UPDATE deadlines450 SET due=?,calc=?,error='',status='manuel',updated=? WHERE id=?",
                        (forced.isoformat(), json.dumps(calc, ensure_ascii=False), desk.now(), ident))
        after = {'due': forced.isoformat(), 'status': 'manuel'}
    else:
        start = start_date or row['start_date']
        try:
            date.fromisoformat(start)
        except ValueError:
            raise Stop('date_invalide') from None
        rid = rule_id or row['rule_id']
        if engine.rule(rid)['start_event'] != row['start_event']:
            raise Stop('regle_incompatible_avec_evenement')
        dist = row['distance'] if distance is None else distance
        regime = row['regime_date'] if regime_date is None else regime_date
        due, calc, error = _compute_fields(rid, start, dist, bool(row['alsace']), regime)
        desk.db.execute('UPDATE deadlines450 SET rule_id=?,start_date=?,distance=?,regime_date=?,due=?,calc=?,error=?,status=?,updated=? WHERE id=?',
                        (rid, start, dist, regime or '', due, calc, error, 'a_completer' if error else 'confirmee', desk.now(), ident))
        after = {'rule': rid, 'start': start, 'due': due}
    _journal(desk, ident, 'correction', reason, before, after)
    desk.db.commit()
    state = sync_calendar(desk, dav, ident)
    desk.audit('echeance_450_corrigee', {'id': ident[:12], 'reason': reason[:80]})
    return public(_row(desk, ident)) | {'calendar': state}


def close(desk, dav, ident, outcome, reason=''):
    if outcome not in ('terminee', 'annulee'):
        raise Stop('statut_invalide')
    reason = _need_reason(reason) if outcome == 'annulee' else str(reason or '')[:300]
    row = _row(desk, ident)
    if not row:
        raise Stop('echeance_absente')
    desk.db.execute('UPDATE deadlines450 SET status=?,updated=? WHERE id=?', (outcome, desk.now(), ident))
    _journal(desk, ident, outcome, reason, {'status': row['status']}, {'status': outcome})
    desk.db.commit()
    state = sync_calendar(desk, dav, ident) if row['due'] else ''
    return public(_row(desk, ident)) | {'calendar': state}


def mark_act_prepared(desk, ident, prepared=True):
    row = _row(desk, ident)
    if not row:
        raise Stop('echeance_absente')
    desk.db.execute('UPDATE deadlines450 SET act_prepared=?,updated=? WHERE id=?', (int(bool(prepared)), desk.now(), ident))
    _journal(desk, ident, 'acte_prepare' if prepared else 'acte_non_prepare')
    desk.db.commit()
    return public(_row(desk, ident))


def create_manual(desk, dav, matter_id, rule_id, start, distance='none', alsace=False, regime_date='', source_path='', note=''):
    row = create_deadline(desk, matter_id, rule_id, start, distance, alsace, regime_date, source_path, '', 'manuel', 'confirmee', note)
    if row['status'] in ('a_confirmer',):
        row = confirm(desk, dav, row['id'])
    state = sync_calendar(desk, dav, row['id']) if row.get('due') else ''
    return public(_row(desk, row['id'])) | {'calendar': state, 'created_now': row.get('created_now', False)}


def journal(desk, ident, limit=50):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT at,action,reason,before,after FROM deadline_journal450 WHERE deadline_id=? '
                                             'ORDER BY id DESC LIMIT ?', (ident, limit))]


def listing(desk, matter='', include_closed=False):
    ensure_schema(desk)
    sql = 'SELECT * FROM deadlines450'
    clauses, args = [], []
    if matter:
        clauses.append('matter=?')
        args.append(matter)
    if not include_closed:
        clauses.append("status NOT IN ('terminee','annulee')")
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    sql += " ORDER BY CASE WHEN due='' THEN 1 ELSE 0 END, due, created"
    rows = [public(dict(r)) for r in desk.db.execute(sql, args)]
    for r in rows:
        r['matter_label'] = matter_label(desk, r['matter'])
    return rows


# ------------------------------------------------------ inventory + job
def observe_inventory(desk, matter_id, items, today=None, limit=10):
    if not enabled(desk):
        return 0
    ensure_schema(desk)
    today = today or datetime.now(timezone.utc)
    queued = 0
    for path, info in sorted(items.items()):
        if not looks_like_candidate(path):
            continue
        etag = str(info.get('etag', ''))
        ident = digest('obs450|' + path + '|' + etag)
        if desk.db.execute('SELECT 1 FROM deadline_obs450 WHERE id=?', (ident,)).fetchone():
            continue
        stale = False
        try:
            from email.utils import parsedate_to_datetime
            stale = parsedate_to_datetime(str(info.get('modified', ''))) < today - timedelta(days=45)
        except (TypeError, ValueError):
            pass
        now = desk.now()
        desk.db.execute('INSERT INTO deadline_obs450 VALUES(?,?,?,?,?,?,?,?)',
                        (ident, matter_id, path, etag, 'ignored_old' if stale else 'queued', '{}', now, now))
        desk.db.commit()
        if stale:
            continue
        desk.enqueue('analyze_deadline450', {'matter': matter_id, 'path': path, 'etag': etag,
                                             'size': int(info.get('size', 0) or 0)}, priority=22)
        queued += 1
        if queued >= limit:
            break
    return queued


def analyze(desk, args, dav=None, extractor=None, today=None):
    ensure_schema(desk)
    path, etag, mid = str(args.get('path', '')), str(args.get('etag', '')), str(args.get('matter', ''))
    if not _matter(desk, mid) or not looks_like_candidate(path):
        raise Stop('piece_ou_dossier_invalide')
    ident = digest('obs450|' + path + '|' + etag)
    done = desk.db.execute('SELECT status,result FROM deadline_obs450 WHERE id=?', (ident,)).fetchone()
    if done and done['status'] == 'done':
        return json.loads(done['result'])
    from .dav import DAV
    client = dav or DAV(desk.c['nextcloud'])
    name = PurePosixPath(path).name
    stat = client.stat(path) if hasattr(client, 'stat') else {'path': path, 'etag': etag, 'size': int(args.get('size', 0))}
    raw = client.download(stat)
    if extractor is None:
        from .documents import extract
        extractor = lambda data, filename: extract(data, filename, desk.c.get('documents', {}))
    text = extractor(raw, name)
    events = extract_start_events(text, today)
    created, existing = [], []
    for event in events:
        row = create_deadline(desk, mid, event['rule_id'], event['date'].isoformat(), source_path=path,
                              excerpt=event['context']) if event['rule_id'] else None
        if not row:
            continue
        (created if row['created_now'] else existing).append(row['id'])
        if row['created_now'] and row.get('due'):
            sync_calendar(desk, client, row['id'])
    result = {'status': 'prepared' if created else 'blocked', 'matter': mid, 'path': path,
              'evenements': len(events), 'echeances_creees': len(created), 'deja_connues': len(existing),
              'message': ('%d échéance(s) proposée(s) à confirmer depuis « %s »' % (len(created), name)) if created else
              'Aucun événement de départ exploitable dans « %s » : aucune échéance créée.' % name}
    now = desk.now()
    desk.db.execute('INSERT OR REPLACE INTO deadline_obs450 VALUES(?,?,?,?,?,?,?,?)',
                    (ident, mid, path, etag, 'done', json.dumps(result, ensure_ascii=False), now, now))
    desk.db.commit()
    if created:
        from .live430 import emit
        emit(desk, 'echeance', result['message'] + ' (dossier %s)' % matter_label(desk, mid), matter=mid,
             dedupe='deadline450-new|' + ident)
    desk.audit('echeances_450_analysees', {'matter': mid, 'path': path, 'created': len(created)})
    return result


# ------------------------------------------------------ cross-check
def has_prepared_act(desk, row):
    if row['act_prepared']:
        return True
    try:
        from . import production420
        production420.ensure_schema(desk)
        found = desk.db.execute("SELECT 1 FROM production_deliverables_v420 WHERE matter=? AND status IN ('prepared','verifying','verified') "
                                "AND job_kind<>'analyze_notice440' AND created>=? LIMIT 1", (row['matter'], row['created'])).fetchone()
        return bool(found)
    except Exception:
        return False


def cross_check(desk, dav=None, today=None):
    """Anomalies : date manquante, agenda absent ou divergent, deux dates contradictoires, échéance sans acte préparé."""
    ensure_schema(desk)
    today = today or date.today()
    rows = [public(dict(r)) for r in desk.db.execute("SELECT * FROM deadlines450 WHERE status NOT IN ('terminee','annulee')")]
    anomalies = []

    def add(kind, row, message, **extra):
        anomalies.append({'kind': kind, 'id': row['id'], 'matter': row['matter'], 'matter_label': matter_label(desk, row['matter']),
                          'message': message, 'rule_label': row['rule_label'], **extra})

    for row in rows:
        if row['status'] == 'a_completer' or not row['due']:
            add('date_manquante', row, 'Échéance sans date calculable : %s' % (row['error'] or 'information manquante') )
    groups = {}
    for row in rows:
        if row['due'] and row['status'] in ACTIVE:
            groups.setdefault((row['matter'], row['rule_id']), []).append(row)
    for (_, _), group in groups.items():
        if len({g['due'] for g in group}) > 1:
            for g in group:
                add('deux_dates', g, 'Deux dates différentes pour « %s » dans ce dossier : %s.' % (
                    g['rule_label'], ' / '.join(sorted({x['due'] for x in group}))))
    urls, tz_name = _calendar_urls(desk)
    events_by_uid = {}
    calendar_ok = False
    if urls and dav is not None and rows:
        dues = [date.fromisoformat(r['due']) for r in rows if r['due']]
        if dues:
            tz = ZoneInfo(tz_name)
            lo = datetime.combine(min(dues) - timedelta(days=400), datetime.min.time(), tz)
            hi = datetime.combine(max(dues) + timedelta(days=2), datetime.min.time(), tz)
            try:
                for event in dav.events(urls, lo, hi, tz_name):
                    events_by_uid.setdefault(event.get('uid', ''), []).append(event)
                calendar_ok = True
            except Stop:
                calendar_ok = False
    for row in rows:
        if not row['due'] or row['status'] not in ACTIVE:
            continue
        if calendar_ok:
            uid = 'axiorhub-%s@mail-agent.local' % row['id']
            found = events_by_uid.get(uid, [])
            dates = {str(e.get('start', ''))[:10] for e in found}
            if not found and date.fromisoformat(row['due']) >= today - timedelta(days=1):
                add('agenda_absent', row, 'L’échéance du %s n’est pas dans l’agenda.' % row['due_label'])
            elif found and row['due'] not in dates:
                add('agenda_divergent', row, 'L’agenda indique %s alors que l’échéance calculée est %s.' % (', '.join(sorted(dates)), row['due']))
        days = (date.fromisoformat(row['due']) - today).days
        if 0 <= days <= 14 and not has_prepared_act(desk, row):
            add('sans_acte', row, 'Échéance dans %d jour(s) sans acte ni brouillon préparé.' % days, days=days)
        if days < 0:
            add('depassee', row, 'Échéance dépassée depuis %d jour(s) et non clôturée.' % -days, days=days)
    return {'anomalies': anomalies, 'agenda_verifie': calendar_ok, 'count': len(anomalies), 'checked': len(rows)}


# ---------------------------------------------------------- reminders
def run_reminders(desk, today=None):
    """Rappels J-30, J-14, J-7, J-2, jour J ; un seul rappel par franchissement, jamais répété."""
    ensure_schema(desk)
    from .live430 import emit
    today = today or date.today()
    sent = 0
    for r in desk.db.execute("SELECT * FROM deadlines450 WHERE status IN ('a_confirmer','confirmee','manuel') AND due<>''").fetchall():
        row = dict(r)
        days = (date.fromisoformat(row['due']) - today).days
        label = matter_label(desk, row['matter'])
        reached = [o for o in OFFSETS if days <= o] if days >= 0 else []
        for offset in reached:
            kind = 'J%s' % offset
            if desk.db.execute('SELECT 1 FROM deadline_reminders450 WHERE deadline_id=? AND kind=?', (row['id'], kind)).fetchone():
                continue
            desk.db.execute('INSERT INTO deadline_reminders450 VALUES(?,?,?)', (row['id'], kind, desk.now()))
            if offset == min(reached):   # seul le rappel le plus proche est émis ; les paliers franchis sont soldés
                rule = engine.rule(row['rule_id'])['label']
                emit(desk, 'echeance', '%s : %s – %s, échéance le %s%s.' % (
                    OFFSET_LABELS[offset], rule, label, engine.fr_date(date.fromisoformat(row['due'])),
                    ' (à confirmer)' if row['status'] == 'a_confirmer' else ''), matter=row['matter'],
                    dedupe='deadline450|%s|%s' % (row['id'], kind))
                sent += 1
        if 0 <= days <= 7 and not has_prepared_act(desk, row):
            kind = 'sans_acte'
            if not desk.db.execute('SELECT 1 FROM deadline_reminders450 WHERE deadline_id=? AND kind=?', (row['id'], kind)).fetchone():
                desk.db.execute('INSERT INTO deadline_reminders450 VALUES(?,?,?)', (row['id'], kind, desk.now()))
                emit(desk, 'echeance_alerte', 'ALERTE : %s – %s, échéance le %s dans %d jour(s), sans acte ni brouillon préparé.' % (
                    engine.rule(row['rule_id'])['label'], label, engine.fr_date(date.fromisoformat(row['due'])), days),
                    matter=row['matter'], dedupe='deadline450|%s|%s' % (row['id'], kind))
                sent += 1
        if days < 0:
            kind = 'depassee'
            if not desk.db.execute('SELECT 1 FROM deadline_reminders450 WHERE deadline_id=? AND kind=?', (row['id'], kind)).fetchone():
                desk.db.execute('INSERT INTO deadline_reminders450 VALUES(?,?,?)', (row['id'], kind, desk.now()))
                emit(desk, 'echeance_alerte', 'ÉCHÉANCE DÉPASSÉE : %s – %s (%s). Clôturer ou corriger.' % (
                    engine.rule(row['rule_id'])['label'], label, row['due']), matter=row['matter'],
                    dedupe='deadline450|%s|%s' % (row['id'], kind))
                sent += 1
    desk.db.commit()
    return sent


def daily_check(desk, dav=None, today=None):
    """Appelé périodiquement par la surveillance : rappels puis contrôle croisé."""
    if not enabled(desk):
        return {'abstention': 'Échéances désactivées.'}
    sent = run_reminders(desk, today)
    report = cross_check(desk, dav, today)
    desk.setting('deadlines450:last_check', {'at': desk.now(), 'anomalies': report['count'], 'reminders': sent})
    return {'reminders': sent, 'anomalies': report['count'], 'remote_write': False}


def today_block(desk, link, today=None, limit=5):
    """Encart HTML de la page « Aujourd'hui » : prochaines échéances et anomalies."""
    from html import escape as esc
    today = today or date.today()
    ensure_schema(desk)
    rows = [r for r in listing(desk) if r['due']][:limit]
    report = cross_check(desk, None, today)
    if not rows and not report['anomalies']:
        return ''
    lines = ''
    for r in rows:
        days = (date.fromisoformat(r['due']) - today).days
        when = 'dépassée' if days < 0 else ('aujourd’hui' if days == 0 else 'dans %d j' % days)
        lines += '<li><strong>%s</strong> (%s) – %s – %s%s</li>' % (
            esc(r['due_label']), esc(when), esc(r['rule_label']), esc(r['matter_label']),
            ' <em>à confirmer</em>' if r['status'] == 'a_confirmer' else '')
    alert = ''
    if report['anomalies']:
        alert = '<p><strong>%d point(s) à vérifier</strong> sur vos échéances.</p>' % len(report['anomalies'])
    return ('<section class="panel" aria-label="Échéances"><h2>Échéances de procédure</h2>%s<ul>%s</ul>'
            '<p><a href="%s">Ouvrir les échéances</a></p></section>') % (alert, lines, esc(link, quote=True))

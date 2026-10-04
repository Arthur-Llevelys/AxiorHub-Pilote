"""Recherche unique 4.9.0 : courriels, fichiers, agenda et notes, avec filtres dossier/date et extraits sourcés.

Principes :
  - tout est interrogé EN LOCAL (index plein texte SQLite FTS5) : aucun appel réseau, aucun modèle, aucun service
    externe pendant une recherche ; la requête n'est jamais écrite dans les journaux ;
  - budget de temps : la recherche est interrompue à 2,5 s et le résultat est alors marqué « partiel » ;
  - chaque résultat porte sa source (type, dossier, date, chemin ou identifiant) et un extrait où les termes trouvés
    sont repérés par des segments (jamais du HTML : l'interface construit le texte, rien n'est injecté) ;
  - l'index complémentaire (courriels, agenda, notes) est stocké dans l'état du cabinet, droits 0600, et peut être
    vidé en un clic ; l'index des fichiers est celui qui existe déjà (documents.sqlite3), lu en lecture seule.

Limites assumées : recherche lexicale (mots et préfixes, accents et casse ignorés), pas de sémantique ; les courriels
sont indexés progressivement (les plus récents d'abord) ; un courriel supprimé de la messagerie ne disparaît de
l'index qu'à sa reconstruction.
"""
from .common import matter_display
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import unicodedata

from .common import Stop, load_matters

KINDS = {'courriel': 'Courriels', 'fichier': 'Fichiers', 'agenda': 'Agenda', 'note': 'Notes et faits'}
TIME_BUDGET = 2.5
MAX_QUERY = 200
MAX_RESULTS = 60
EXCERPT_WORDS = 28
MAIL_BATCH = 60
MAIL_TIME = 25.0
BODY_LIMIT = 8000

SCHEMA = '''
CREATE TABLE IF NOT EXISTS s490(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL, matter TEXT NOT NULL DEFAULT '', title TEXT NOT NULL,
  date TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}', sig TEXT NOT NULL,
  UNIQUE(kind, ref));
CREATE INDEX IF NOT EXISTS s490_matter ON s490(matter, date);
CREATE INDEX IF NOT EXISTS s490_date ON s490(date);
CREATE VIRTUAL TABLE IF NOT EXISTS s490_fts USING fts5(title, body, content='s490', content_rowid='id',
  tokenize="unicode61 remove_diacritics 2");
CREATE TRIGGER IF NOT EXISTS s490_ai AFTER INSERT ON s490 BEGIN
  INSERT INTO s490_fts(rowid, title, body) VALUES (new.id, new.title, new.body); END;
CREATE TRIGGER IF NOT EXISTS s490_ad AFTER DELETE ON s490 BEGIN
  INSERT INTO s490_fts(s490_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body); END;
CREATE TRIGGER IF NOT EXISTS s490_au AFTER UPDATE ON s490 BEGIN
  INSERT INTO s490_fts(s490_fts, rowid, title, body) VALUES ('delete', old.id, old.title, old.body);
  INSERT INTO s490_fts(rowid, title, body) VALUES (new.id, new.title, new.body); END;
CREATE TABLE IF NOT EXISTS mail_cursor490(
  folder TEXT NOT NULL, validity TEXT NOT NULL, high INTEGER NOT NULL, low INTEGER NOT NULL, backfilled INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(folder, validity));
CREATE TABLE IF NOT EXISTS timing490(at TEXT NOT NULL, ms INTEGER NOT NULL, results INTEGER NOT NULL, partial INTEGER NOT NULL);
'''


def fold(text):
    text = unicodedata.normalize('NFKD', str(text or '').replace('’', "'").replace('œ', 'oe'))
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


def _path(desk):
    return Path(desk.c['state_dir']) / 'search490.sqlite3'


def connect(desk):
    path = _path(desk)
    new = not path.exists()
    if new:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(fd)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript(SCHEMA)
    db.commit()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return db


# ----------------------------------------------------------------------------------------------- requête
def build_query(q):
    """Texte libre → expression FTS5 sûre : chaque mot devient un préfixe, les guillemets font des expressions exactes."""
    q = str(q or '').strip()
    if not q:
        raise Stop('recherche_vide')
    if len(q) > MAX_QUERY:
        raise Stop('recherche_trop_longue')
    terms = []
    for phrase, word in re.findall(r'"([^"]{2,100})"|([^\s"]+)', q):
        if phrase:
            words = re.findall(r"[\w']+", phrase)
            if words:
                terms.append('"%s"' % ' '.join(w.replace('"', '') for w in words))
        else:
            for w in re.findall(r"[\w]+", word):
                if len(w) >= 2:
                    terms.append('"%s"*' % w if len(w) >= 3 else '"%s"' % w)
    terms = terms[:12]
    if not terms:
        raise Stop('recherche_sans_terme')
    return terms


def _segments(snippet):
    out, pos = [], 0
    for m in re.finditer('\x01(.*?)\x02', snippet, re.DOTALL):
        if m.start() > pos:
            out.append({'t': snippet[pos:m.start()], 'hit': False})
        out.append({'t': m.group(1), 'hit': True})
        pos = m.end()
    if pos < len(snippet):
        out.append({'t': snippet[pos:], 'hit': False})
    clean = [{'t': re.sub(r'[\x01\x02]', '', s['t']), 'hit': s['hit']} for s in out if s['t']]
    return clean


def _guard(db, deadline):
    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20000)


def _iso_day(value, end=False):
    if not value:
        return ''
    try:
        d = date.fromisoformat(str(value))
    except ValueError:
        raise Stop('periode_invalide') from None
    return d.isoformat() + ('T23:59:59' if end else '')


def _matter_labels(desk):
    return {m['id']: matter_display(m) for m in load_matters(desk.c)}


# --------------------------------------------------------------------------------------------- fichiers
def _files(desk, expr, matter, d_from, d_to, deadline, limit):
    path = Path(desk.c['state_dir']) / 'documents.sqlite3'
    if not path.is_file():
        return [], False
    db = sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    partial = False
    try:
        _guard(db, deadline)
        sql = ('SELECT c.matter,c.source_id,c.path,c.modified,c.kind,snippet(knowledge_fts,4,char(1),char(2),\'…\',%d) AS snip,'
               'bm25(knowledge_fts,1.0,1.0,1.0,4.0,1.0) AS rank FROM knowledge_fts JOIN knowledge_chunks c '
               'ON c.matter=knowledge_fts.matter AND c.source_id=knowledge_fts.source_id AND c.chunk_no=knowledge_fts.chunk_no '
               'WHERE knowledge_fts MATCH ?') % EXCERPT_WORDS
        params = [expr]
        if matter:
            sql += ' AND c.matter=?'
            params.append(matter)
        if d_from:
            sql += ' AND c.modified>=?'
            params.append(d_from)
        if d_to:
            sql += ' AND c.modified<=?'
            params.append(d_to)
        sql += ' ORDER BY rank LIMIT ?'
        params.append(limit * 4)
        try:
            rows = db.execute(sql, params).fetchall()
        except sqlite3.OperationalError as ex:
            if 'interrupt' in str(ex).lower():
                return [], True
            return [], False
    finally:
        db.close()
    seen, out = set(), []
    for r in rows:
        key = (r['matter'], r['source_id'])
        if key in seen:
            continue
        seen.add(key)
        out.append({'kind': 'fichier', 'matter': r['matter'], 'title': r['path'].rsplit('/', 1)[-1], 'date': str(r['modified'] or '')[:10],
                    'segments': _segments(r['snip']), 'source': {'label': r['path'], 'path': r['path'], 'type': r['kind']}, 'rank': r['rank']})
        if len(out) >= limit:
            break
    return out, partial


# ------------------------------------------------------------------------- courriels, agenda et notes
def _local(db, expr, kinds, matter, d_from, d_to, deadline, limit):
    _guard(db, deadline)
    sql = ('SELECT s.kind,s.ref,s.matter,s.title,s.date,s.meta,snippet(s490_fts,1,char(1),char(2),\'…\',%d) AS snip,'
           'bm25(s490_fts,6.0,1.0) AS rank FROM s490_fts JOIN s490 s ON s.id=s490_fts.rowid WHERE s490_fts MATCH ?') % EXCERPT_WORDS
    params = [expr]
    if kinds:
        sql += ' AND s.kind IN (%s)' % ','.join('?' * len(kinds))
        params += list(kinds)
    if matter:
        sql += ' AND s.matter=?'
        params.append(matter)
    if d_from:
        sql += ' AND s.date>=?'
        params.append(d_from)
    if d_to:
        sql += ' AND s.date<=?'
        params.append(d_to)
    sql += ' ORDER BY rank LIMIT ?'
    params.append(limit)
    try:
        rows = db.execute(sql, params).fetchall()
    except sqlite3.OperationalError as ex:
        return [], 'interrupt' in str(ex).lower()
    out = []
    for r in rows:
        meta = json.loads(r['meta'] or '{}')
        out.append({'kind': r['kind'], 'matter': r['matter'], 'title': r['title'] or '(sans titre)', 'date': str(r['date'])[:10],
                    'segments': _segments(r['snip']), 'source': {'label': meta.get('label', ''), 'ref': r['ref'], **{k: v for k, v in meta.items() if k != 'label'}},
                    'rank': r['rank']})
    return out, False


def search(desk, q, matter='', date_from='', date_to='', kinds=None, limit=30):
    started = time.monotonic()
    deadline = started + TIME_BUDGET
    terms = build_query(q)
    limit = max(1, min(int(limit), MAX_RESULTS))
    d_from, d_to = _iso_day(date_from), _iso_day(date_to, True)
    if d_from and d_to and d_from > d_to:
        raise Stop('periode_invalide')
    wanted = [k for k in (kinds or list(KINDS)) if k in KINDS]
    if not wanted:
        raise Stop('type_recherche_invalide')
    if matter and matter not in {m['id'] for m in load_matters(desk.c)}:
        raise Stop('dossier_absent')
    refresh_local(desk)
    db = connect(desk)
    partial = False
    try:
        results, relaxed = [], False
        for joiner in (' AND ', ' OR '):
            expr = joiner.join(terms)
            results, partial_local = [], False
            local_kinds = [k for k in wanted if k != 'fichier']
            if local_kinds:
                if time.monotonic() > deadline:
                    partial_local = True
                else:
                    got, partial_local = _local(db, expr, local_kinds, matter, d_from, d_to, deadline, limit)
                    results += got
            if 'fichier' in wanted:
                if time.monotonic() > deadline:
                    partial_local = True
                else:
                    got, partial_files = _files(desk, expr, matter, d_from, d_to, deadline, limit)
                    results += got
                    partial_local = partial_local or partial_files
            partial = partial or partial_local
            if results or len(terms) == 1 or partial:
                relaxed = bool(results) and joiner == ' OR '
                break
        # fusion par rang réciproque : chaque source garde son ordre de pertinence
        by_kind, fused = {}, []
        for r in sorted(results, key=lambda x: x['rank']):
            by_kind.setdefault(r['kind'], 0)
            fused.append((1.0 / (20 + by_kind[r['kind']]), r))
            by_kind[r['kind']] += 1
        fused.sort(key=lambda x: -x[0])
        labels = _matter_labels(desk)
        final = []
        for _, r in fused[:limit]:
            r.pop('rank', None)
            r['matter_label'] = labels.get(r['matter'], r['matter'])
            final.append(r)
        counts = {k: sum(1 for r in results if r['kind'] == k) for k in KINDS}
        ms = int((time.monotonic() - started) * 1000)
        db.execute('INSERT INTO timing490 VALUES(?,?,?,?)', (datetime.now(timezone.utc).isoformat(), ms, len(final), int(partial)))
        db.execute('DELETE FROM timing490 WHERE rowid NOT IN (SELECT rowid FROM timing490 ORDER BY rowid DESC LIMIT 200)')
        db.commit()
        return {'results': final, 'counts': counts, 'elapsed_ms': ms, 'partial': partial,
                'relaxed': relaxed,
                'note': (('Recherche interrompue après %.1f s : résultats partiels, précisez la requête ou un dossier.' % TIME_BUDGET) if partial else
                         ('Aucun résultat ne contient tous les mots : voici ceux qui en contiennent certains.' if relaxed else '')),
                'filters': {'matter': matter, 'from': date_from, 'to': date_to, 'kinds': wanted}}
    finally:
        db.close()


# -------------------------------------------------------------------------------------- indexation locale
def _sig(*parts):
    return hashlib.sha256('\x1f'.join(str(p) for p in parts).encode('utf-8', 'replace')).hexdigest()


def _upsert(db, kind, ref, matter, title, when, body, meta):
    sig = _sig(matter, title, when, body, json.dumps(meta, sort_keys=True))
    row = db.execute('SELECT id,sig FROM s490 WHERE kind=? AND ref=?', (kind, ref)).fetchone()
    if row and row['sig'] == sig:
        return 0
    if row:
        db.execute('UPDATE s490 SET matter=?,title=?,date=?,body=?,meta=?,sig=? WHERE id=?',
                   (matter, title, when, body, json.dumps(meta, ensure_ascii=False), sig, row['id']))
    else:
        db.execute('INSERT INTO s490(kind,ref,matter,title,date,body,meta,sig) VALUES(?,?,?,?,?,?,?,?)',
                   (kind, ref, matter, title, when, body, json.dumps(meta, ensure_ascii=False), sig))
    return 1


def _prune(db, kind, refs, prefix=''):
    keep = set(refs)
    for row in db.execute('SELECT ref FROM s490 WHERE kind=? AND ref LIKE ?', (kind, prefix + '%')).fetchall():
        if row['ref'] not in keep:
            db.execute('DELETE FROM s490 WHERE kind=? AND ref=?', (kind, row['ref']))


def _flatten(value, depth=0):
    if depth > 4:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ' '.join(_flatten(v, depth + 1) for k, v in value.items() if k not in ('sources', 'source_ids'))
    if isinstance(value, list):
        return ' '.join(_flatten(v, depth + 1) for v in value[:60])
    return ''


def refresh_local(desk, force=False):
    """Met à jour, depuis l'état interne, les entrées d'agenda, de notes et les objets des courriels suivis. Quelques ms."""
    last = float(desk.settings('search490:local_refresh', 0))
    if not force and time.time() - last < 30:
        return {'changed': 0}
    db = connect(desk)
    changed = 0
    try:
        ddb = desk.db
        refs = []
        # agenda : cache CalDAV
        try:
            for r in ddb.execute('SELECT id,title,description,location,starts,matter FROM calendar_cache'):
                ref = 'cal|' + r['id']
                refs.append(ref)
                changed += _upsert(db, 'agenda', ref, r['matter'] or '', r['title'], str(r['starts'])[:19],
                                   ' '.join(x for x in (r['description'], r['location']) if x)[:4000],
                                   {'label': 'Événement de l’agenda', 'type': 'evenement'})
        except sqlite3.Error:
            pass
        try:
            from . import echeances450
            labels = {x['id']: x['label'] for x in echeances450.rules_for_ui()} if hasattr(echeances450, 'rules_for_ui') else {}
        except Exception:
            labels = {}
        try:
            for r in ddb.execute("SELECT id,matter,rule_id,due,note,source_excerpt,status FROM deadlines450 WHERE due<>''"):
                ref = 'ech|' + r['id']
                refs.append(ref)
                changed += _upsert(db, 'agenda', ref, r['matter'], 'Échéance : ' + (labels.get(r['rule_id']) or r['rule_id'].replace('_', ' ')),
                                   r['due'], ' '.join(x for x in (r['note'], r['source_excerpt'], r['status']) if x),
                                   {'label': 'Échéance de procédure', 'type': 'echeance', 'status': r['status']})
        except sqlite3.Error:
            pass
        try:
            for r in ddb.execute('SELECT id,matter,title,due,status FROM tasks'):
                ref = 'tache|' + r['id']
                refs.append(ref)
                changed += _upsert(db, 'agenda', ref, r['matter'] or '', 'Tâche : ' + r['title'], r['due'] or '', r['status'] or '',
                                   {'label': 'Tâche', 'type': 'tache'})
        except sqlite3.Error:
            pass
        for row in db.execute("SELECT ref FROM s490 WHERE kind='agenda' AND (ref LIKE 'cal|%' OR ref LIKE 'ech|%' OR ref LIKE 'tache|%')").fetchall():
            if row['ref'] not in set(refs):
                db.execute("DELETE FROM s490 WHERE kind='agenda' AND ref=?", (row['ref'],))
        # notes et faits
        refs = []
        try:
            for r in ddb.execute("SELECT id,matter,category,text,status,updated FROM case_facts WHERE COALESCE(status,'')<>'archived'"):
                ref = 'fait|' + r['id']
                refs.append(ref)
                changed += _upsert(db, 'note', ref, r['matter'], 'Fait : ' + (r['category'] or 'dossier'), str(r['updated'] or '')[:19], r['text'] or '',
                                   {'label': 'Fait retenu pour le dossier', 'type': 'fait'})
        except sqlite3.Error:
            pass
        try:
            for r in ddb.execute('SELECT key,data,updated FROM notes'):
                try:
                    data = json.loads(r['data'])
                except ValueError:
                    continue
                body = _flatten(data.get('result', data))[:4000]
                if not body.strip():
                    continue
                ref = 'note|' + r['key']
                refs.append(ref)
                changed += _upsert(db, 'note', ref, '', 'Note interne : ' + str(data.get('source_subject', ''))[:160], str(r['updated'] or '')[:19], body,
                                   {'label': 'Note interne de l’agent', 'type': 'note_interne', 'mail_key': r['key']})
        except sqlite3.Error:
            pass
        for row in db.execute("SELECT ref FROM s490 WHERE kind='note'").fetchall():
            if row['ref'] not in set(refs):
                db.execute("DELETE FROM s490 WHERE kind='note' AND ref=?", (row['ref'],))
        # courriels suivis (objet, expéditeur) : disponibles même sans lecture de la messagerie
        refs = []
        try:
            for r in ddb.execute('SELECT mail_key,subject,sender,matter,received,state FROM work_items'):
                ref = 'suivi|' + r['mail_key']
                refs.append(ref)
                changed += _upsert(db, 'courriel', ref, r['matter'] or '', r['subject'] or '(sans objet)', str(r['received'] or '')[:19],
                                   'De : %s' % (r['sender'] or ''), {'label': 'Courriel suivi par l’agent', 'type': 'suivi', 'state': r['state'],
                                                                      'mail_key': r['mail_key']})
        except sqlite3.Error:
            pass
        for row in db.execute("SELECT ref FROM s490 WHERE kind='courriel' AND ref LIKE 'suivi|%'").fetchall():
            if row['ref'] not in set(refs):
                db.execute("DELETE FROM s490 WHERE kind='courriel' AND ref=?", (row['ref'],))
        db.commit()
    finally:
        db.close()
    desk.setting('search490:local_refresh', time.time())
    return {'changed': changed}


# ------------------------------------------------------------------------------------- courriels (IMAP)
def _mail_enabled(desk):
    return bool(desk.settings('search490:mail', True))


def _store_mail(db, desk, mail, folder, account):
    msg = mail.msg
    text = (mail.text or '')[:BODY_LIMIT]
    key = mail.key(account)
    row = desk.db.execute('SELECT matter FROM work_items WHERE mail_key=?', (key,)).fetchone()
    matter = row[0] if row and row[0] else ''
    if not matter and getattr(mail, 'msg', None) is not None:
        from .mailbox import draft_key
        dk = draft_key(msg)
        if dk:
            r2 = desk.db.execute('SELECT matter FROM work_items WHERE mail_key=?', (dk,)).fetchone()
            matter = r2[0] if r2 and r2[0] else ''
    ref = 'imap|%s|%s|%s' % (folder, mail.uidvalidity, mail.uid)
    meta = {'label': 'Courriel — dossier « %s »' % folder, 'type': 'imap', 'folder': folder, 'uid': mail.uid, 'validity': mail.uidvalidity,
            'from': str(msg.get('From', ''))[:200], 'to': str(msg.get('To', ''))[:300],
            'draft': folder == desk.c['mail']['drafts']}
    body = 'De : %s À : %s\n%s' % (meta['from'], meta['to'], text)
    return _upsert(db, 'courriel', ref, matter, str(msg.get('Subject', '') or '(sans objet)')[:300], mail.timestamp.isoformat()[:19], body, meta)


def collect_mail(desk, batch=MAIL_BATCH, max_seconds=MAIL_TIME, box_factory=None):
    """Lit en LECTURE SEULE les messages récents puis, par passes successives, les plus anciens. Aucune écriture IMAP."""
    if not _mail_enabled(desk):
        return {'indexed': 0, 'reason': 'desactive'}
    from .mailbox import Mailbox
    started = time.monotonic()
    box = (box_factory or Mailbox)(desk.c['mail'])
    db = connect(desk)
    indexed = errors = 0
    account = desk.c['mail']['username'] + '@' + desk.c['mail']['host']
    def read(folder, uid):
        nonlocal indexed, errors
        try:
            mail = box.fetch(folder, str(uid))
            _store_mail(db, desk, mail, folder, account)
            indexed += 1
            return True
        except Stop:
            errors += 1
            return True          # un message illisible est écarté, il ne bloque pas le curseur

    try:
        for folder in dict.fromkeys([desk.c['mail']['inbox'], desk.c['mail']['sent'], desk.c['mail']['drafts']]):
            if indexed >= batch or time.monotonic() - started > max_seconds:
                break
            validity = box.select(folder)
            cur = db.execute('SELECT * FROM mail_cursor490 WHERE folder=? AND validity=?', (folder, validity)).fetchone()
            if cur is None:
                uids = sorted(int(u) for u in box.search(folder, 'UNDELETED'))
                if not uids:
                    continue
                chosen = list(reversed(uids[-max(1, batch - indexed):]))        # les plus récents d'abord
                done = []
                for uid in chosen:
                    if time.monotonic() - started > max_seconds:
                        break
                    read(folder, uid)
                    done.append(uid)
                if not done:
                    continue
                high, low = max(done), min(done)
                back = int(low <= uids[0])
            else:
                high, low, back = cur['high'], cur['low'], cur['backfilled']
                newer = sorted(int(u) for u in box.search(folder, 'UNDELETED', 'UID', '%d:*' % (high + 1)) if int(u) > high)
                for uid in newer[:max(1, batch - indexed)]:
                    if time.monotonic() - started > max_seconds:
                        break
                    read(folder, uid)
                    high = uid
                if not back and indexed < batch and time.monotonic() - started <= max_seconds:
                    older = sorted((int(u) for u in box.search(folder, 'UNDELETED', 'UID', '1:%d' % max(1, low - 1)) if int(u) < low), reverse=True)
                    if not older:
                        back = 1
                    for uid in older[:batch - indexed]:
                        if time.monotonic() - started > max_seconds:
                            break
                        read(folder, uid)
                        low = uid
                    if older and low == older[-1]:
                        back = 1
            db.execute('INSERT OR REPLACE INTO mail_cursor490 VALUES(?,?,?,?,?)', (folder, validity, high, low, back))
            db.commit()
    finally:
        db.close()
        try:
            box.close()
        except Exception:
            pass
    desk.setting('search490:mail_run', time.time())
    return {'indexed': indexed, 'errors': errors}


def collect_calendar(desk):
    """Rafraîchit le cache d'agenda sur une fenêtre large (lecture CalDAV seule)."""
    try:
        from .workplan import calendar_events, _calendar_urls
        if not _calendar_urls(desk):
            return {'calendar': 'non_configure'}
        today = date.today()
        calendar_events(desk, (today - timedelta(days=120)).isoformat(), (today + timedelta(days=400)).isoformat(), refresh=True)
        return {'calendar': 'ok'}
    except Exception:
        return {'calendar': 'indisponible'}


def job(desk, args=None):
    out = {}
    out.update(collect_calendar(desk))
    out.update(refresh_local(desk, force=True))
    try:
        out.update(collect_mail(desk))
    except Stop as ex:
        out['mail'] = str(ex)
    except Exception:
        out['mail'] = 'indisponible'
    return out


def schedule(desk, now=None):
    """Appelé par la maintenance : met un passage d'indexation en file toutes les 10 minutes."""
    now = time.time() if now is None else now
    if now - float(desk.settings('search490:scheduled', 0)) < 600:
        return False
    desk.setting('search490:scheduled', now)
    pending = desk.db.execute("SELECT 1 FROM jobs WHERE kind='search_index490' AND status IN ('pending','running')").fetchone()
    if pending:
        return False
    desk.enqueue('search_index490', priority=80)
    return True


# --------------------------------------------------------------------------------------------- réglages
def status(desk):
    db = connect(desk)
    try:
        counts = {k: db.execute('SELECT COUNT(*) FROM s490 WHERE kind=?', (k,)).fetchone()[0] for k in KINDS}
        mail = db.execute("SELECT COUNT(*) FROM s490 WHERE kind='courriel' AND ref LIKE 'imap|%'").fetchone()[0]
        oldest = db.execute("SELECT MIN(date) FROM s490 WHERE kind='courriel' AND ref LIKE 'imap|%'").fetchone()[0]
        backfill = [dict(r) for r in db.execute('SELECT folder,backfilled FROM mail_cursor490')]
        times = sorted(r['ms'] for r in db.execute('SELECT ms FROM timing490 ORDER BY rowid DESC LIMIT 100'))
    finally:
        db.close()
    files = 0
    p = Path(desk.c['state_dir']) / 'documents.sqlite3'
    if p.is_file():
        try:
            c = sqlite3.connect('file:%s?mode=ro' % p, uri=True, timeout=5)
            files = c.execute('SELECT COUNT(DISTINCT matter||source_id) FROM knowledge_chunks').fetchone()[0]
            c.close()
        except sqlite3.Error:
            files = 0
    counts['fichier'] = files
    pct = lambda p_: times[min(len(times) - 1, int(len(times) * p_))] if times else None
    return {'counts': counts, 'mail_indexing': _mail_enabled(desk), 'mail_indexed': mail, 'mail_oldest': (oldest or '')[:10],
            'backfill_done': bool(backfill) and all(b['backfilled'] for b in backfill),
            'timing': {'samples': len(times), 'median_ms': pct(0.5), 'p95_ms': pct(0.95), 'max_ms': times[-1] if times else None},
            'budget_ms': int(TIME_BUDGET * 1000), 'target_ms': 3000}


def set_mail_indexing(desk, enabled):
    desk.setting('search490:mail', bool(enabled))
    desk.audit('recherche_490_indexation_courriels', {'enabled': bool(enabled)})
    return status(desk)


def purge(desk, confirm=''):
    if confirm != 'yes':
        raise Stop('confirmation_requise')
    db = connect(desk)
    try:
        db.execute('DELETE FROM s490')
        db.execute("INSERT INTO s490_fts(s490_fts) VALUES('rebuild')")
        db.execute('DELETE FROM mail_cursor490')
        db.commit()
    finally:
        db.close()
    desk.setting('search490:local_refresh', 0)
    desk.audit('recherche_490_index_vide', {})
    return status(desk)

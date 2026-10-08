"""5.6.14 (M14, A05, C06) : chaîne de preuve des données du dossier.

Instantané figé des sources d'une mission : pour chaque fichier, identifiant stable (dossier + chemin), version distante (etag),
hash du contenu lu, type, date ; pour chaque courriel, identifiant de message, compte, date ; pour chaque événement, UID, origine et
caractère confirmé ou indicatif. Chaque lecture conserve la pagination (pages extraites, blanches, illisibles). Le manifeste
distingue inventorié, extrait, lu intégralement, illisible et exclu ; deux homonymes restent distincts ; le total réel est conservé.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re

from .common import Stop, clean_path, digest, fold, load_matters, matter_display, under

READABLE = ('.pdf', '.docx', '.odt', '.txt', '.md', '.csv', '.eml', '.rtf')
SCHEMA = '''CREATE TABLE IF NOT EXISTS snapshots5614(
 id TEXT PRIMARY KEY, mission TEXT NOT NULL, matter TEXT NOT NULL, data TEXT NOT NULL, content_hash TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS readings5614(
 id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, source_id TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL,
 pages_total INTEGER NOT NULL, pages_text INTEGER NOT NULL, pages_blank INTEGER NOT NULL, pages_unreadable INTEGER NOT NULL,
 chars INTEGER NOT NULL, text TEXT NOT NULL, created TEXT NOT NULL);'''


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def _sha(raw):
    return hashlib.sha256(raw if isinstance(raw, bytes) else str(raw).encode()).hexdigest()


def source_id(matter_id, path):
    return 'f-' + digest(str(matter_id) + '|' + clean_path(path))[:16]


def snapshot(desk, matter_id, mission='', dav=None, limit_files=2000):
    """Inventaire figé : fichiers (identifiant stable, version distante), courriels rattachés, événements d'agenda."""
    ensure_schema(desk)
    matter = next((m for m in load_matters(desk.c) if str(m['id']) == str(matter_id)), None)
    if not matter:
        raise Stop('dossier_absent')
    files, notes = [], []
    try:
        from .document_projects import _dav, _inventory
        client = dav or _dav(desk)
        items = [x for x in _inventory(client, matter['path']) if not x.get('directory')]
        for x in items[:limit_files]:
            path = clean_path(x['path'])
            files.append({'id': source_id(matter['id'], path), 'kind': 'file', 'path': path, 'name': PurePosixPath(path).name,
                          'relative': path[len(matter['path']):].lstrip('/'), 'version': str(x.get('etag', '') or ''), 'size': int(x.get('size') or 0),
                          'modified': str(x.get('modified', '') or '')[:32], 'readable': PurePosixPath(path).suffix.lower() in READABLE,
                          'status': 'inventorie'})
        if len(items) > limit_files:
            notes.append('%d fichier(s) au-delà de la limite d’inventaire (%d) : non inventoriés, signalés.' % (len(items) - limit_files, limit_files))
        total_files = len(items)
    except Stop as ex:
        notes.append('Inventaire Nextcloud indisponible : ' + str(ex))
        total_files = None
    except Exception as ex:
        notes.append('Inventaire Nextcloud indisponible : ' + str(ex)[:80])
        total_files = None
    mails = []
    try:
        for r in desk.db.execute('SELECT folder,uid,sender,mail_date FROM portfolio_mail_links WHERE matter=? ORDER BY mail_date DESC LIMIT 200', (matter['id'],)):
            mails.append({'id': 'm-' + digest('%s|%s|%s' % (matter['id'], r['folder'], r['uid']))[:16], 'kind': 'mail', 'folder': r['folder'], 'uid': str(r['uid']),
                          'sender': r['sender'], 'date': str(r['mail_date'])[:19], 'status': 'inventorie'})
    except Exception:
        notes.append('Courriels rattachés non lus (table absente).')
    events = []
    try:
        for r in desk.db.execute('SELECT id,uid,title,starts,ends,location,etag,fetched FROM calendar_cache WHERE matter=? ORDER BY starts LIMIT 100', (matter['id'],)):
            events.append({'id': 'e-' + digest(matter['id'] + '|' + str(r['uid']))[:16], 'kind': 'event', 'uid': r['uid'], 'title': r['title'][:200], 'starts': r['starts'][:19],
                           'ends': (r['ends'] or '')[:19], 'location': (r['location'] or '')[:120], 'version': r['etag'] or '', 'fetched': (r['fetched'] or '')[:19],
                           'certainty': 'inscrit_agenda', 'status': 'inventorie',
                           'note': 'Date inscrite à l’agenda du cabinet : son caractère officiel (fixation par le greffe) reste à confirmer par une pièce.'})
    except Exception:
        notes.append('Agenda non lu (table absente).')
    data = {'matter': matter['id'], 'matter_label': matter_display(matter), 'root': matter['path'], 'files': files, 'mails': mails, 'events': events,
            'total_files': total_files if total_files is not None else len(files), 'notes': notes, 'created': desk.now()}
    content_hash = _sha(json.dumps({'files': [(f['path'], f['version'], f['size']) for f in files], 'mails': [(m['folder'], m['uid']) for m in mails],
                                    'events': [(e['uid'], e['version']) for e in events]}, sort_keys=True))
    sid = digest('snapshot5614|' + matter['id'] + '|' + content_hash + '|' + mission)[:32]
    desk.db.execute('INSERT OR IGNORE INTO snapshots5614 VALUES(?,?,?,?,?,?)', (sid, mission, matter['id'], json.dumps(data, ensure_ascii=False), content_hash, desk.now()))
    desk.db.commit()
    data['id'] = sid
    data['content_hash'] = content_hash
    return data


def get_snapshot(desk, sid):
    ensure_schema(desk)
    r = desk.db.execute('SELECT * FROM snapshots5614 WHERE id=?', (str(sid),)).fetchone()
    if not r:
        raise Stop('instantane_absent')
    data = json.loads(r['data'])
    data['id'] = r['id']
    data['content_hash'] = r['content_hash']
    return data


def changed_since(desk, snapshot_data, dav=None):
    """Delta entre l'instantané et l'état actuel (nouveaux fichiers, versions modifiées, disparus)."""
    current = snapshot(desk, snapshot_data['matter'], dav=dav)
    before = {f['path']: f for f in snapshot_data.get('files', [])}
    after = {f['path']: f for f in current.get('files', [])}
    added = [p for p in after if p not in before]
    removed = [p for p in before if p not in after]
    modified = [p for p in after if p in before and (after[p]['version'] != before[p]['version'] or after[p]['size'] != before[p]['size'])]
    mails_before = {(m['folder'], m['uid']) for m in snapshot_data.get('mails', [])}
    new_mails = [m for m in current.get('mails', []) if (m['folder'], m['uid']) not in mails_before]
    return {'added': added, 'removed': removed, 'modified': modified, 'new_mails': len(new_mails), 'changed': bool(added or removed or modified or new_mails),
            'current_snapshot': current['id']}


def read_file(desk, client, matter_id, path, snapshot_id='', max_chars=2_000_000):
    """Lecture intégrale avec couverture par page ; l'illisible et le chiffré sont signalés, jamais ignorés."""
    ensure_schema(desk)
    from .documents import extract, extract_pages
    path = clean_path(path)
    sid = source_id(matter_id, path)
    name = PurePosixPath(path).name
    try:
        raw = client.download(client.stat(path))
    except Stop as ex:
        return {'source_id': sid, 'path': path, 'status': 'indisponible', 'error': str(ex), 'text': '', 'pages': [], 'coverage': {'total': 0, 'text': 0, 'blank': 0, 'unreadable': 0}}
    cfg = {**desk.c.get('documents', {}), 'max_document_chars': max_chars, 'max_document_chars_long': max_chars}
    pages, text, status = [], '', 'lu_integralement'
    try:
        if name.lower().endswith(('.pdf', '.docx')):
            try:
                pages = extract_pages(raw, name, cfg)
            except Stop:
                raise
            except Exception as ex:   # rendu LibreOffice indisponible (Windows, conteneur minimal) : section logique unique, jamais une page inventée
                try:
                    out = extract(raw, name, cfg)
                    text0 = out if isinstance(out, str) else out.get('text', '')
                except Stop:
                    raise
                except Exception:
                    return {'source_id': sid, 'path': path, 'name': name, 'status': 'illisible', 'error': 'extraction_indisponible:' + str(ex)[:60], 'text': '', 'pages': [], 'sha256': _sha(raw),
                            'coverage': {'total': 0, 'text': 0, 'blank': 0, 'unreadable': 0}}
                pages = [{'page': 1, 'text': text0, 'extraction': 'logical_section' if text0.strip() else 'unreadable'}]
            text = '\n'.join('[Page %s]\n%s' % (p['page'], p['text']) for p in pages)
        else:
            out = extract(raw, name, cfg)
            text = out if isinstance(out, str) else out.get('text', '')
            pages = [{'page': 1, 'text': text, 'extraction': 'text' if text.strip() else 'unreadable'}]
    except Stop as ex:
        status = 'illisible' if 'chiffr' not in str(ex) and 'password' not in str(ex) else 'exclu'
        return {'source_id': sid, 'path': path, 'status': status, 'error': str(ex), 'text': '', 'pages': [], 'sha256': _sha(raw),
                'coverage': {'total': 0, 'text': 0, 'blank': 0, 'unreadable': 0}}
    blank = sum(1 for p in pages if p.get('extraction') == 'blank' or (not str(p.get('text', '')).strip() and p.get('extraction') != 'unreadable'))
    unreadable = sum(1 for p in pages if p.get('extraction') == 'unreadable' or (not str(p.get('text', '')).strip() and p.get('extraction') == 'unreadable'))
    with_text = sum(1 for p in pages if str(p.get('text', '')).strip())
    if not with_text:
        status = 'illisible'
    elif unreadable:
        status = 'lu_partiellement'
    row = {'source_id': sid, 'path': path, 'name': name, 'status': status, 'sha256': _sha(raw), 'text': text, 'chars': len(text),
           'pages': [{'page': p['page'], 'chars': len(str(p.get('text', ''))), 'extraction': p.get('extraction', '')} for p in pages],
           'coverage': {'total': len(pages), 'text': with_text, 'blank': blank, 'unreadable': unreadable}}
    desk.db.execute('INSERT OR REPLACE INTO readings5614 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (digest(snapshot_id + '|' + sid)[:32], snapshot_id, sid, path, row['sha256'], status, len(pages), with_text, blank, unreadable, len(text), text[:max_chars], desk.now()))
    desk.db.commit()
    return row


def manifest(snapshot_data, readings=(), extracts=(), selected=()):
    """Manifeste probant : statut par fichier, total réel, couverture par page, exclusions motivées, homonymes distincts."""
    files = {f['path']: dict(f) for f in snapshot_data.get('files', [])}
    by_id = {f['id']: f for f in files.values()}
    for r in readings or ():
        f = by_id.get(r.get('source_id')) or files.get(r.get('path'))
        if f is not None:
            f['status'] = r.get('status', 'lu_integralement')
            f['sha256'] = r.get('sha256', '')
            f['coverage'] = r.get('coverage', {})
            if r.get('error'):
                f['exclusion'] = r['error']
    for path in extracts or ():
        f = files.get(clean_path(str(path)))
        if f is not None and f['status'] == 'inventorie':
            f['status'] = 'extrait'
    for path in selected or ():
        f = files.get(clean_path(str(path)))
        if f is not None:
            f['selected'] = True
    counts = {}
    for f in files.values():
        counts[f['status']] = counts.get(f['status'], 0) + 1
    names = {}
    for f in files.values():
        names.setdefault(fold(f['name']), []).append(f['path'])
    homonyms = {n: p for n, p in names.items() if len(p) > 1}
    conclusions = sorted([f for f in files.values() if 'conclusion' in fold(f['name'])], key=lambda f: f.get('modified', ''), reverse=True)
    return {'total_files': snapshot_data.get('total_files', len(files)), 'inventoried': len(files), 'counts': counts,
            'files': sorted(files.values(), key=lambda f: f['path']), 'homonyms': homonyms,
            'conclusions_candidates': [{'path': f['path'], 'name': f['name'], 'version': f['version'], 'modified': f.get('modified', ''), 'status': f['status']} for f in conclusions[:10]],
            'mails': len(snapshot_data.get('mails', [])), 'events': len(snapshot_data.get('events', [])), 'notes': snapshot_data.get('notes', []),
            'unread': [f['path'] for f in files.values() if f['status'] == 'inventorie'][:200],
            'excluded': [{'path': f['path'], 'reason': f.get('exclusion', f['status'])} for f in files.values() if f['status'] in ('illisible', 'exclu', 'indisponible')],
            'snapshot': snapshot_data.get('id', ''), 'content_hash': snapshot_data.get('content_hash', '')}


def summary_text(man):
    parts = ['%d fichier(s) inventorié(s) sur %s' % (man['inventoried'], man['total_files'])]
    for status, label in (('lu_integralement', 'lu(s) intégralement'), ('lu_partiellement', 'lu(s) partiellement'), ('extrait', 'extrait(s)'), ('inventorie', 'non lu(s)'),
                          ('illisible', 'illisible(s)'), ('exclu', 'exclu(s)'), ('indisponible', 'indisponible(s)')):
        if man['counts'].get(status):
            parts.append('%d %s' % (man['counts'][status], label))
    if man['homonyms']:
        parts.append('%d nom(s) de fichier en double, chemins distincts' % len(man['homonyms']))
    return ' · '.join(parts)

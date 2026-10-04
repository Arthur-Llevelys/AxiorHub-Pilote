"""Agenda et tâches 5.2.0 : modification prudente des événements et tâches Nextcloud personnels, rappels hors de l'interface.

Écriture prudente :
  - réglage explicite « Autoriser la modification de mes agendas et tâches personnels » (désactivé par défaut) ;
  - seul l'objet visé est relu puis réécrit, et seules les propriétés modifiées (titre, dates, état) sont remplacées :
    rappels (VALARM), catégories, participants, description, propriétés inconnues sont conservés tels quels ;
  - écriture conditionnée à la version lue (If-Match / ETag) : un objet modifié ailleurs entre-temps n'est jamais écrasé ;
  - les séries récurrentes (RRULE, RDATE) et les occurrences modifiées (RECURRENCE-ID) restent en lecture seule ;
  - aucune suppression d'un objet personnel.
"""
from datetime import date, datetime, timedelta, timezone
import re
import urllib.parse as U
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import Stop

SETTING = 'agenda520:edit_personal'


def enabled(desk):
    return bool(desk.settings(SETTING, False))


def set_enabled(desk, value):
    desk.setting(SETTING, bool(value))
    desk.audit('agenda_520_modification_personnelle', {'enabled': bool(value)})
    return {'enabled': bool(value)}


# ------------------------------------------------------------------------------------------------ iCalendar
def unfold(ics):
    return re.sub(r'\r?\n[ \t]', '', ics)


def fold_line(line):
    raw = line.encode('utf-8')
    if len(raw) <= 75:
        return line
    out, cur = [], b''
    for ch in line:
        b = ch.encode('utf-8')
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode('utf-8'))
            cur = b''
        cur += b
    out.append(cur.decode('utf-8'))
    return '\r\n '.join(out)


def escape_text(value):
    return str(value).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\r', '').replace('\n', '\\n')


def _name(line):
    return re.split(r'[;:]', line, 1)[0].upper()


def _params(line):
    head = line.split(':', 1)[0]
    return head.split(';')[1:]


def component(lines, comp):
    """(début, fin) du composant unique ``comp`` ; refuse s'il y en a plusieurs (série avec exceptions)."""
    starts = [i for i, l in enumerate(lines) if l.strip().upper() == 'BEGIN:' + comp]
    if len(starts) != 1:
        raise Stop('evenement_recurrent_lecture_seule' if len(starts) > 1 else 'objet_agenda_illisible')
    start = starts[0]
    depth = 0
    for i in range(start + 1, len(lines)):
        up = lines[i].strip().upper()
        if up.startswith('BEGIN:'):
            depth += 1
        elif up == 'END:' + comp and depth == 0:
            return start, i
        elif up.startswith('END:'):
            depth -= 1
    raise Stop('objet_agenda_illisible')


def top_props(lines, a, b):
    """Indices des propriétés de premier niveau (hors VALARM et autres sous-composants)."""
    out, depth = [], 0
    for i in range(a + 1, b):
        up = lines[i].strip().upper()
        if up.startswith('BEGIN:'):
            depth += 1
        elif up.startswith('END:'):
            depth -= 1
        elif depth == 0 and ':' in lines[i]:
            out.append(i)
    return out


def get_prop(lines, idx, name):
    for i in idx:
        if _name(lines[i]) == name:
            return lines[i]
    return None


def _tz(desk):
    try:
        return ZoneInfo(desk.c.get('calendar', {}).get('timezone', 'Europe/Paris'))
    except ZoneInfoNotFoundError:
        return timezone.utc


def format_like(name, original, value, desk):
    """Nouvelle ligne ``name`` pour ``value`` (date ou datetime), dans le même style que l'original (date seule, TZID, UTC, flottant)."""
    params = _params(original) if original else []
    if isinstance(value, date) and not isinstance(value, datetime):
        return '%s;VALUE=DATE:%s' % (name, value.strftime('%Y%m%d'))
    if any(p.upper() == 'VALUE=DATE' for p in params):
        return '%s;VALUE=DATE:%s' % (name, value.date().strftime('%Y%m%d'))
    tzid = next((p.split('=', 1)[1] for p in params if p.upper().startswith('TZID=')), '')
    if tzid:
        try:
            local = value.astimezone(ZoneInfo(tzid.strip('"')))
            return '%s;TZID=%s:%s' % (name, tzid, local.strftime('%Y%m%dT%H%M%S'))
        except ZoneInfoNotFoundError:
            pass
    if original and not original.rstrip().upper().endswith('Z') and not tzid:
        return '%s:%s' % (name, value.astimezone(_tz(desk)).replace(tzinfo=None).strftime('%Y%m%dT%H%M%S'))
    if not original:
        tz = _tz(desk)
        key = getattr(tz, 'key', '')
        if key:
            return '%s;TZID=%s:%s' % (name, key, value.astimezone(tz).strftime('%Y%m%dT%H%M%S'))
    return '%s:%s' % (name, value.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))


def parse_value(line, desk):
    """date ou datetime (aware) d'une ligne DTSTART/DTEND/DUE."""
    params = _params(line)
    value = line.split(':', 1)[1].strip()
    if any(p.upper() == 'VALUE=DATE' for p in params) or re.fullmatch(r'\d{8}', value):
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    dt = datetime.strptime(value[:15], '%Y%m%dT%H%M%S')
    if value.endswith('Z'):
        return dt.replace(tzinfo=timezone.utc)
    tzid = next((p.split('=', 1)[1].strip('"') for p in params if p.upper().startswith('TZID=')), '')
    try:
        return dt.replace(tzinfo=ZoneInfo(tzid)) if tzid else dt.replace(tzinfo=_tz(desk))
    except ZoneInfoNotFoundError:
        return dt.replace(tzinfo=_tz(desk))


def patch(ics, comp, changes, desk):
    """Remplace uniquement les propriétés listées ({NOM: ligne complète ou None pour supprimer}) ; met à jour DTSTAMP, LAST-MODIFIED, SEQUENCE."""
    lines = unfold(ics).replace('\r\n', '\n').split('\n')
    while lines and not lines[-1].strip():
        lines.pop()
    a, b = component(lines, comp)
    idx = top_props(lines, a, b)
    if any(_name(lines[i]) in ('RRULE', 'RDATE', 'RECURRENCE-ID', 'EXDATE') for i in idx):
        raise Stop('evenement_recurrent_lecture_seule')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    changes = dict(changes)
    changes['DTSTAMP'] = 'DTSTAMP:' + stamp
    changes['LAST-MODIFIED'] = 'LAST-MODIFIED:' + stamp
    seq = get_prop(lines, idx, 'SEQUENCE')
    try:
        changes['SEQUENCE'] = 'SEQUENCE:%d' % (int(seq.split(':', 1)[1].strip()) + 1 if seq else 1)
    except ValueError:
        changes['SEQUENCE'] = 'SEQUENCE:1'
    # Les propriétés nouvelles se placent avant le premier sous-composant (VALARM…), comme l'exige RFC 5545.
    insert_at = next((i for i in range(a + 1, b) if lines[i].strip().upper().startswith('BEGIN:')), b)
    present = {_name(lines[i]) for i in idx}
    keep, placed = [], set()
    for i in range(len(lines)):
        if a < i < b and i in idx and _name(lines[i]) in changes:
            name = _name(lines[i])
            if name not in placed and changes[name] is not None:
                keep.append(changes[name])
            placed.add(name)
            continue
        if i == insert_at:
            keep += [v for k, v in changes.items() if k not in present and v is not None]
        keep.append(lines[i])
    return '\r\n'.join(fold_line(l) for l in keep) + '\r\n'


# ------------------------------------------------------------------------------------------------ Nextcloud
def _client(desk, dav=None):
    from .workplan import _dav
    return dav or _dav(desk)


def _object_url(client, calendar_url, href):
    base = client.calendar_url(calendar_url)
    url = U.urljoin(client.http.base + '/', href) if href.startswith('/') else base + href
    if not url.startswith(base) or '..' in U.unquote(U.urlsplit(url).path).split('/'):
        raise Stop('objet_agenda_hors_calendrier')
    return url


def _get(client, url):
    return client.http.request('GET', url, None, {}, 2_000_000).decode('utf-8', 'replace')


def _put(client, url, ics, etag):
    if not etag:
        raise Stop('version_agenda_inconnue')
    try:
        client.http.request('PUT', url, ics.encode('utf-8'), {'Content-Type': 'text/calendar; charset=utf-8', 'If-Match': etag}, 2_000_000)
    except Stop as ex:
        if str(ex) == 'http_412':
            raise Stop('objet_agenda_modifie_ailleurs') from None
        if str(ex) == 'http_403':
            raise Stop('agenda_lecture_seule_pour_le_compte') from None
        raise


def _title(value):
    value = re.sub(r'\s+', ' ', str(value or '')).strip()
    if not 1 <= len(value) <= 300:
        raise Stop('titre_evenement_invalide')
    return value


def edit_event(desk, args, dav=None):
    if not enabled(desk):
        raise Stop('modification_agenda_personnel_desactivee')
    from .workplan import ensure_schema, calendar_events
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM calendar_cache WHERE id=?', (str(args.get('event', '')),)).fetchone()
    if not row:
        raise Stop('evenement_absent')
    if row['recurrence_id']:
        raise Stop('evenement_recurrent_lecture_seule')
    client = _client(desk, dav)
    url = _object_url(client, row['calendar_url'], row['href'])
    ics = _get(client, url)
    lines = unfold(ics).replace('\r\n', '\n').split('\n')
    a, b = component(lines, 'VEVENT')
    idx = top_props(lines, a, b)
    old_start, old_end, old_dur = get_prop(lines, idx, 'DTSTART'), get_prop(lines, idx, 'DTEND'), get_prop(lines, idx, 'DURATION')
    changes = {'SUMMARY': 'SUMMARY:' + escape_text(_title(args.get('title') or row['title']))}
    start_raw = str(args.get('start', '') or '')
    if start_raw:
        all_day = bool(old_start) and isinstance(parse_value(old_start, desk), date) and not isinstance(parse_value(old_start, desk), datetime)
        if all_day:
            try:
                new_day = date.fromisoformat(start_raw[:10])
            except ValueError:
                raise Stop('creneau_evenement_invalide') from None
            first = parse_value(old_start, desk)
            span = (parse_value(old_end, desk) - first).days if old_end else 1
            changes['DTSTART'] = format_like('DTSTART', old_start, new_day, desk)
            if old_end:
                changes['DTEND'] = format_like('DTEND', old_end, new_day + timedelta(days=max(1, span)), desk)
        else:
            try:
                begins = datetime.fromisoformat(start_raw)
                minutes = int(args.get('duration_minutes') or 60)
            except (TypeError, ValueError):
                raise Stop('creneau_evenement_invalide') from None
            if not 5 <= minutes <= 1440:
                raise Stop('creneau_evenement_invalide')
            if not begins.tzinfo:
                begins = begins.replace(tzinfo=_tz(desk))
            changes['DTSTART'] = format_like('DTSTART', old_start, begins, desk)
            if old_dur and not old_end:
                changes['DURATION'] = 'DURATION:PT%dM' % minutes
            else:
                changes['DTEND'] = format_like('DTEND', old_end or old_start, begins + timedelta(minutes=minutes), desk)
    new = patch(ics, 'VEVENT', changes, desk)
    _put(client, url, new, row['etag'])
    desk.db.execute('DELETE FROM calendar_cache WHERE id=?', (row['id'],))
    desk.db.commit()
    try:
        moment = datetime.fromisoformat(row['starts'])
        calendar_events(desk, (moment - timedelta(days=40)).isoformat(), (moment + timedelta(days=40)).isoformat(), refresh=True, dav=client)
    except (Stop, OSError, ValueError):
        pass
    desk.audit('agenda_520_evenement_personnel_modifie', {'event': row['id'][:16]})
    return {'event': row['id'], 'message': 'Événement modifié dans votre agenda Nextcloud (rappels et autres propriétés conservés).'}


TASK_STATUS = {'todo': ('NEEDS-ACTION', 0), 'in_progress': ('IN-PROCESS', 50), 'completed': ('COMPLETED', 100), 'cancelled': ('CANCELLED', 0)}


def edit_task(desk, args, dav=None):
    if not enabled(desk):
        raise Stop('modification_agenda_personnel_desactivee')
    from .workplan import ensure_schema, sync_tasks
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM work_tasks_v211 WHERE id=?', (str(args.get('task', '')),)).fetchone()
    if not row:
        raise Stop('tache_absente')
    client = _client(desk, dav)
    url = _object_url(client, row['calendar_url'], row['href'])
    ics = _get(client, url)
    lines = unfold(ics).replace('\r\n', '\n').split('\n')
    a, b = component(lines, 'VTODO')
    idx = top_props(lines, a, b)
    changes = {}
    if args.get('title'):
        changes['SUMMARY'] = 'SUMMARY:' + escape_text(_title(args['title']))
    if 'due' in args:
        due = str(args.get('due') or '')
        if due:
            try:
                day = date.fromisoformat(due[:10])
            except ValueError:
                raise Stop('date_tache_invalide') from None
            old = get_prop(lines, idx, 'DUE')
            if old and isinstance(parse_value(old, desk), datetime):
                keep = parse_value(old, desk)
                value = datetime.combine(day, keep.timetz().replace(tzinfo=None), keep.tzinfo)
            else:
                value = day
            changes['DUE'] = format_like('DUE', old, value, desk)
        else:
            changes['DUE'] = None
    status = str(args.get('status', '') or '')
    if status:
        if status not in TASK_STATUS:
            raise Stop('etat_tache_invalide')
        code, percent = TASK_STATUS[status]
        changes['STATUS'] = 'STATUS:' + code
        changes['PERCENT-COMPLETE'] = 'PERCENT-COMPLETE:%d' % percent
        changes['COMPLETED'] = ('COMPLETED:' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')) if status == 'completed' else None
    if not changes:
        raise Stop('aucune_modification')
    new = patch(ics, 'VTODO', changes, desk)
    _put(client, url, new, row['etag'])
    try:
        sync_tasks(desk, client)
    except (Stop, OSError, ValueError):
        desk.db.execute("UPDATE work_tasks_v211 SET etag='' WHERE id=?", (row['id'],))
        desk.db.commit()
    desk.audit('agenda_520_tache_personnelle_modifiee', {'task': row['id'][:16], 'status': status})
    return {'task': row['id'], 'message': 'Tâche modifiée dans Nextcloud (rappels, catégories et autres propriétés conservés).'}


def perform(desk, kind, args):
    if kind == 'edit_personal_event':
        return edit_event(desk, args)
    if kind == 'edit_personal_task':
        return edit_task(desk, args)
    raise Stop('action_inconnue')

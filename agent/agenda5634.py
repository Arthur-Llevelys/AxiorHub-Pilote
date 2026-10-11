"""5.6.34 : agenda local du cabinet — utilisable sans aucun agenda CalDAV, la synchronisation devient une option.

- Mode par défaut : agenda LOCAL (base du poste ou du serveur) : rendez-vous, audiences, échéances et tâches datées, rattachement aux
  dossiers, rappels, sauvegarde quotidienne au format iCalendar et export à la demande. Aucune donnée ne quitte la machine.
- Option : synchronisation vers le calendrier Nextcloud de planification déjà configuré (``nextcloud_workflow``) ; un échec de
  synchronisation n'empêche jamais l'enregistrement local (état « à synchroniser », nouvel essai au cycle suivant).
- Import : fichier .ics (export Nextcloud, Google Agenda, Outlook…) ou adresse iCal HTTPS (« adresse secrète » Google, lien
  d'abonnement Nextcloud) ; l'adresse n'est pas conservée ; les doublons (même UID et même début) sont ignorés ; les séries simples
  (FREQ DAILY/WEEKLY/MONTHLY/YEARLY avec INTERVAL, COUNT, UNTIL, BYDAY hebdomadaire) sont développées sur 18 mois.
- Les événements locaux sont reflétés dans le cache d'agenda (``calendar_cache``, calendrier « local:agenda ») : la fiche d'audience,
  le briefing, l'organisation de la semaine et les alertes les voient comme les autres.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid
from zoneinfo import ZoneInfo

from .common import Stop, load_matters

LOCAL_URL = 'local:agenda'
KINDS = {'rendez_vous': 'Rendez-vous', 'audience': 'Audience', 'echeance': 'Échéance', 'tache': 'Tâche datée', 'autre': 'Autre'}
REMINDERS = (0, 10, 30, 60, 120, 1440, 2880, 10080)
BACKUPS_KEPT = 30
HORIZON_DAYS = 548


def _now():
    return datetime.now(timezone.utc)


def _tz(desk):
    try:
        return ZoneInfo(str((desk.c.get('calendar') or {}).get('timezone') or (desk.c.get('planning') or {}).get('timezone') or 'Europe/Paris'))
    except Exception:   # noqa: BLE001
        return ZoneInfo('Europe/Paris')


def schema(desk):
    from .workplan import ensure_schema as calendar_schema
    calendar_schema(desk)
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS agenda5634(id TEXT PRIMARY KEY, uid TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '', starts TEXT NOT NULL, ends TEXT NOT NULL,
      all_day INTEGER NOT NULL DEFAULT 0, matter TEXT NOT NULL DEFAULT '', reminders TEXT NOT NULL DEFAULT '[]',
      status TEXT NOT NULL DEFAULT 'actif', source TEXT NOT NULL DEFAULT 'local', sync_state TEXT NOT NULL DEFAULT 'local',
      remote_uid TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT 'cabinet', created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS agenda5634_period ON agenda5634(status, starts);
    CREATE UNIQUE INDEX IF NOT EXISTS agenda5634_uid ON agenda5634(uid, starts);
    CREATE TABLE IF NOT EXISTS agenda_rappels5634(event TEXT NOT NULL, minutes INTEGER NOT NULL, sent TEXT NOT NULL, PRIMARY KEY(event, minutes));''')


def mode(desk):
    cfg = desk.c.get('nextcloud_workflow') or {}
    sync = bool(cfg.get('enabled') and cfg.get('planning_calendar_url'))
    return {'local': True, 'sync': sync,
            'label': 'Agenda local, synchronisé avec Nextcloud' if sync else 'Agenda local (sans synchronisation)',
            'help': ('Vos rendez-vous et échéances sont enregistrés dans AxiorHub et copiés dans le calendrier Nextcloud de planification.' if sync else
                     'Vos rendez-vous et échéances sont enregistrés dans AxiorHub, sur cette machine, sans serveur d’agenda. La synchronisation '
                     'avec Nextcloud ou Google reste une option (Paramètres › Guide d’installation › Agenda) ; vous pouvez importer un agenda existant.')}


# ------------------------------------------------------------------------------------------------ validation
def _when(desk, data):
    tz = _tz(desk)
    all_day = str(data.get('all_day') or '') in ('1', 'true', 'True', 'on') or data.get('all_day') is True
    raw = str(data.get('start') or '').strip()
    try:
        if all_day:
            day = date.fromisoformat(raw[:10])
            days = max(1, min(int(data.get('days') or 1), 31))
            begins = datetime.combine(day, datetime.min.time(), tz)
            return begins, begins + timedelta(days=days), True
        begins = datetime.fromisoformat(raw)
        minutes = int(data.get('duration_minutes') or 60)
    except (ValueError, TypeError):
        raise Stop('creneau_evenement_invalide') from None
    if not 5 <= minutes <= 1440:
        raise Stop('creneau_evenement_invalide')
    if not begins.tzinfo:
        begins = begins.replace(tzinfo=tz)
    return begins, begins + timedelta(minutes=minutes), False


def _clean(desk, data):
    title = re.sub(r'\s+', ' ', str(data.get('title') or '')).strip()
    if not 2 <= len(title) <= 200:
        raise Stop('titre_evenement_invalide')
    kind = str(data.get('kind') or 'rendez_vous')
    if kind not in KINDS:
        raise Stop('type_evenement_invalide')
    matter = str(data.get('matter') or '')
    if matter and not any(m['id'] == matter for m in load_matters(desk.c)):
        raise Stop('dossier_absent')
    reminders = data.get('reminders')
    if isinstance(reminders, str):
        reminders = [x for x in re.split(r'[,;\s]+', reminders) if x]
    try:
        reminders = sorted({int(x) for x in (reminders if reminders is not None else ([1440, 60] if kind != 'echeance' else [10080, 1440]))})
    except (TypeError, ValueError):
        raise Stop('rappel_invalide') from None
    if any(x not in REMINDERS for x in reminders) or len(reminders) > 4:
        raise Stop('rappel_invalide')
    return {'title': title, 'kind': kind, 'matter': matter, 'location': str(data.get('location') or '').strip()[:200],
            'description': str(data.get('notes') or data.get('description') or '').strip()[:2000], 'reminders': reminders}


# ------------------------------------------------------------------------------------------------ cache commun d'agenda
def _mirror(desk, row):
    """Reflet de l'événement local dans le cache d'agenda commun (vu par la fiche d'audience, le briefing, la planification)."""
    desk.db.execute('DELETE FROM calendar_cache WHERE calendar_url=? AND uid=?', (LOCAL_URL, row['uid']))
    if row['status'] != 'actif':
        return
    eid = hashlib.sha256(('local|' + row['id']).encode()).hexdigest()
    label = KINDS.get(row['kind'], '')
    title = row['title'] if row['kind'] in ('rendez_vous', 'autre') else '%s : %s' % (label, row['title'])
    desk.db.execute('INSERT OR REPLACE INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (eid, row['uid'], '', LOCAL_URL, '', '', title, row['description'], row['location'], row['starts'], row['ends'],
                     0 if row['kind'] == 'echeance' else 1, row['matter'], json.dumps([row['matter']] if row['matter'] else []), desk.now()))


def _row(desk, ident):
    row = desk.db.execute('SELECT * FROM agenda5634 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('evenement_absent')
    return dict(row)


def _push(desk, row):
    """Synchronisation facultative vers le calendrier Nextcloud de planification ; l'échec laisse l'événement « à synchroniser »."""
    m = mode(desk)
    if not m['sync']:
        return 'local'
    try:
        from .workplan import _dav
        client = _dav(desk)
        calendar = (desk.c.get('nextcloud_workflow') or {})['planning_calendar_url']
        event_id = hashlib.sha256(('agenda5634|' + row['id']).encode()).hexdigest()
        if row['status'] != 'actif':
            if row['remote_uid']:
                client.delete_event(calendar, row['remote_uid'], '')
            return 'synchronise'
        uid = client.put_event(calendar, event_id, row['title'], datetime.fromisoformat(row['starts']), datetime.fromisoformat(row['ends']),
                               ('Événement de l’agenda AxiorHub.\nDossier : %s\n%s' % (row['matter'] or 'non relié', row['description']))[:2000],
                               replace=bool(row['remote_uid']))
        desk.db.execute('UPDATE agenda5634 SET remote_uid=? WHERE id=?', (str(uid or ''), row['id']))
        return 'synchronise'
    except Exception:   # noqa: BLE001 — jamais bloquant : l'agenda local fait foi
        return 'a_synchroniser'


def create(desk, data, owner='cabinet', source='local', uid=''):
    schema(desk)
    clean = _clean(desk, data)
    begins, ends, all_day = _when(desk, data)
    ident = uuid.uuid4().hex
    uid = uid or 'axiorhub-local-%s@agenda.local' % ident
    stamp = desk.now()
    row = {'id': ident, 'uid': uid, 'kind': clean['kind'], 'title': clean['title'], 'description': clean['description'], 'location': clean['location'],
           'starts': begins.astimezone(timezone.utc).isoformat(), 'ends': ends.astimezone(timezone.utc).isoformat(), 'all_day': int(all_day),
           'matter': clean['matter'], 'reminders': json.dumps(clean['reminders']), 'status': 'actif', 'source': source, 'sync_state': 'local',
           'remote_uid': '', 'owner': owner, 'created': stamp, 'updated': stamp}
    try:
        desk.db.execute('INSERT INTO agenda5634(%s) VALUES(%s)' % (','.join(row), ','.join('?' for _ in row)), tuple(row.values()))
    except Exception as ex:   # noqa: BLE001
        if 'UNIQUE' in str(ex):
            raise Stop('evenement_deja_present') from None
        raise
    _mirror(desk, row)
    if source == 'local':
        row['sync_state'] = _push(desk, row)
        desk.db.execute('UPDATE agenda5634 SET sync_state=? WHERE id=?', (row['sync_state'], ident))
    desk.db.commit()
    desk.audit('agenda5634_cree', {'id': ident, 'kind': row['kind'], 'matter': row['matter'], 'source': source})
    return {'id': ident, 'event': public(row), 'message': 'Événement enregistré dans l’agenda%s.' % (
        ' et copié dans Nextcloud' if row['sync_state'] == 'synchronise' else (' (synchronisation à reprendre)' if row['sync_state'] == 'a_synchroniser' else ''))}


def update(desk, data, owner='cabinet'):
    schema(desk)
    row = _row(desk, data.get('id'))
    if row['status'] != 'actif':
        raise Stop('evenement_annule')
    clean = _clean(desk, {**row, 'notes': row['description'], 'reminders': json.loads(row['reminders']), **data})
    begins, ends, all_day = _when(desk, {**data, 'start': data.get('start') or row['starts']})
    row.update(clean)
    row.update(starts=begins.astimezone(timezone.utc).isoformat(), ends=ends.astimezone(timezone.utc).isoformat(), all_day=int(all_day),
               reminders=json.dumps(clean['reminders']), updated=desk.now())
    row['sync_state'] = _push(desk, row)
    desk.db.execute('UPDATE agenda5634 SET kind=?,title=?,description=?,location=?,starts=?,ends=?,all_day=?,matter=?,reminders=?,sync_state=?,updated=? WHERE id=?',
                    (row['kind'], row['title'], row['description'], row['location'], row['starts'], row['ends'], row['all_day'], row['matter'],
                     row['reminders'], row['sync_state'], row['updated'], row['id']))
    desk.db.execute('DELETE FROM agenda_rappels5634 WHERE event=?', (row['id'],))   # nouvel horaire : rappels à nouveau dus
    _mirror(desk, row)
    desk.db.commit()
    desk.audit('agenda5634_modifie', {'id': row['id']})
    return {'id': row['id'], 'event': public(row), 'message': 'Événement modifié.'}


def cancel(desk, ident):
    schema(desk)
    row = _row(desk, ident)
    if row['status'] == 'annule':
        return {'id': row['id'], 'message': 'Événement déjà annulé.'}
    row['status'] = 'annule'
    row['sync_state'] = _push(desk, row)
    desk.db.execute("UPDATE agenda5634 SET status='annule',sync_state=?,updated=? WHERE id=?", (row['sync_state'], desk.now(), row['id']))
    _mirror(desk, row)
    desk.db.commit()
    desk.audit('agenda5634_annule', {'id': row['id']})
    return {'id': row['id'], 'message': 'Événement annulé (conservé dans l’historique et les sauvegardes).'}


def public(row):
    out = {k: row[k] for k in ('id', 'kind', 'title', 'description', 'location', 'starts', 'ends', 'matter', 'status', 'source', 'sync_state')}
    out['all_day'] = bool(row['all_day'])
    out['reminders'] = json.loads(row['reminders']) if isinstance(row['reminders'], str) else row['reminders']
    out['kind_label'] = KINDS.get(row['kind'], row['kind'])
    return out


def listing(desk, start=None, end=None, matter='', limit=500):
    schema(desk)
    a = (start or (_now() - timedelta(days=1))).astimezone(timezone.utc).isoformat()
    b = (end or (_now() + timedelta(days=60))).astimezone(timezone.utc).isoformat()
    sql = "SELECT * FROM agenda5634 WHERE status='actif' AND starts<? AND ends>?" + (' AND matter=?' if matter else '') + ' ORDER BY starts LIMIT ?'
    args = (b, a) + ((matter,) if matter else ()) + (int(limit),)
    return [public(dict(r)) for r in desk.db.execute(sql, args)]


# ------------------------------------------------------------------------------------------------ iCalendar
def _esc(text):
    return str(text or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def _fold(line):
    out, data = [], line.encode('utf-8')
    while len(data) > 75:
        cut = 75
        while cut > 0 and (data[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(data[:cut].decode('utf-8'))
        data = b' ' + data[cut:]
    out.append(data.decode('utf-8'))
    return '\r\n'.join(out)


def export_ics(desk, include_cancelled=False):
    schema(desk)
    rows = desk.db.execute('SELECT * FROM agenda5634' + ('' if include_cancelled else " WHERE status='actif'") + ' ORDER BY starts').fetchall()
    stamp = _now().strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//AxiorHub Pilote//Agenda local//FR', 'CALSCALE:GREGORIAN']
    for r in rows:
        a, b = datetime.fromisoformat(r['starts']), datetime.fromisoformat(r['ends'])
        lines += ['BEGIN:VEVENT', 'UID:' + r['uid'], 'DTSTAMP:' + stamp]
        if r['all_day']:
            tz = _tz(desk)
            lines += ['DTSTART;VALUE=DATE:' + a.astimezone(tz).strftime('%Y%m%d'), 'DTEND;VALUE=DATE:' + b.astimezone(tz).strftime('%Y%m%d')]
        else:
            lines += ['DTSTART:' + a.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'DTEND:' + b.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')]
        lines += ['SUMMARY:' + _esc(r['title']), 'CATEGORIES:' + _esc(KINDS.get(r['kind'], r['kind']))]
        if r['location']:
            lines.append('LOCATION:' + _esc(r['location']))
        desc = r['description'] + (('\nDossier : ' + r['matter']) if r['matter'] else '')
        if desc.strip():
            lines.append('DESCRIPTION:' + _esc(desc.strip()))
        if r['status'] != 'actif':
            lines.append('STATUS:CANCELLED')
        lines.append('X-AXIORHUB-KIND:' + r['kind'])
        if r['matter']:
            lines.append('X-AXIORHUB-MATTER:' + _esc(r['matter']))
        for minutes in json.loads(r['reminders']):
            lines += ['BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:' + _esc(r['title']), 'TRIGGER:-PT%dM' % minutes, 'END:VALARM']
        lines.append('END:VEVENT')
    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold(x) for x in lines) + '\r\n'


def _unfold(text):
    return re.sub(r'\r?\n[ \t]', '', str(text or '').replace('\r\n', '\n'))


def _unesc(value):
    return re.sub(r'\\([\\;,nN])', lambda m: '\n' if m.group(1) in 'nN' else m.group(1), value)


def _parse_dt(prop, value, tz):
    params = dict(p.split('=', 1) for p in prop.split(';')[1:] if '=' in p)
    v = value.strip()
    if params.get('VALUE') == 'DATE' or re.fullmatch(r'\d{8}', v):
        return datetime.combine(datetime.strptime(v[:8], '%Y%m%d').date(), datetime.min.time(), tz), True
    if v.endswith('Z'):
        return datetime.strptime(v, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc), False
    zone = tz
    if params.get('TZID'):
        try:
            zone = ZoneInfo(params['TZID'].strip('"'))
        except Exception:   # noqa: BLE001 — fuseau Windows (« Romance Standard Time ») : fuseau du cabinet
            zone = tz
    return datetime.strptime(v[:15], '%Y%m%dT%H%M%S').replace(tzinfo=zone), False


def _expand(begins, rule, limit_end):
    """Occurrences d'une règle RRULE simple (DAILY, WEEKLY [BYDAY], MONTHLY, YEARLY ; INTERVAL, COUNT, UNTIL), bornées à 18 mois."""
    parts = dict(p.split('=', 1) for p in rule.split(';') if '=' in p)
    freq = parts.get('FREQ', '')
    if freq not in ('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'):
        return [begins], False
    interval = max(1, int(parts.get('INTERVAL', '1') or 1))
    count = int(parts['COUNT']) if parts.get('COUNT', '').isdigit() else None
    until = None
    if parts.get('UNTIL'):
        u = parts['UNTIL']
        until = (datetime.strptime(u[:8], '%Y%m%d').replace(tzinfo=begins.tzinfo) + timedelta(days=1)) if len(u) == 8 else \
            datetime.strptime(u[:15], '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
    days = [('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU').index(d[-2:]) for d in parts.get('BYDAY', '').split(',') if d[-2:] in ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')]
    if parts.get('BYDAY') and freq != 'WEEKLY' or any(k in parts for k in ('BYMONTHDAY', 'BYSETPOS', 'BYMONTH', 'BYHOUR')):
        return [begins], False   # règle complexe : première occurrence seulement, signalée
    out, n, k = [], 0, 0
    while len(out) < 500 and k < 5000:
        if freq in ('MONTHLY', 'YEARLY'):
            months = k * interval * (12 if freq == 'YEARLY' else 1)
            year, month = begins.year + (begins.month - 1 + months) // 12, (begins.month - 1 + months) % 12 + 1
            k += 1
            try:
                candidates = [begins.replace(year=year, month=month)]
            except ValueError:   # 31 d'un mois plus court, 29 février : occurrence sautée (RFC 5545)
                continue
        else:
            cursor = begins + (timedelta(days=k * interval) if freq == 'DAILY' else timedelta(weeks=k * interval))
            k += 1
            if freq == 'WEEKLY' and days:
                week = cursor - timedelta(days=cursor.weekday())
                candidates = sorted(week + timedelta(days=d) for d in days)
            else:
                candidates = [cursor]
        for occ in candidates:
            if occ < begins:
                continue
            if (until and occ >= until) or (count is not None and n >= count) or occ > limit_end:
                return out, True
            out.append(occ)
            n += 1
    return out, True


def import_ics(desk, text, label='import', owner='cabinet'):
    """Import d'un fichier iCalendar : événements ajoutés à l'agenda local ; doublons ignorés ; rien n'est envoyé ailleurs."""
    schema(desk)
    raw = _unfold(text)
    if 'BEGIN:VCALENDAR' not in raw.upper():
        raise Stop('fichier_ics_invalide')
    if len(raw) > 8_000_000:
        raise Stop('fichier_ics_trop_volumineux')
    tz = _tz(desk)
    label = re.sub(r'[^\w .-]', '', str(label or 'import'))[:40] or 'import'
    limit_end = _now() + timedelta(days=HORIZON_DAYS)
    added = skipped = cancelled = partial = 0
    for block in re.findall(r'BEGIN:VEVENT\n(.*?)\nEND:VEVENT', raw.replace('\r', ''), flags=re.S):
        props = {}
        for line in re.sub(r'BEGIN:VALARM.*?END:VALARM\n?', '', block, flags=re.S).split('\n'):
            if ':' not in line:
                continue
            key, value = line.split(':', 1)
            props.setdefault(key.split(';')[0].upper(), (key, value))
        if 'DTSTART' not in props:
            continue
        if props.get('STATUS', ('', ''))[1].strip().upper() == 'CANCELLED':
            cancelled += 1
            continue
        try:
            begins, all_day = _parse_dt(*props['DTSTART'], tz)
            if 'DTEND' in props:
                ends, _ = _parse_dt(*props['DTEND'], tz)
            elif 'DURATION' in props:
                m = re.fullmatch(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?', props['DURATION'][1].strip())
                ends = begins + timedelta(days=int(m.group(1) or 0), hours=int(m.group(2) or 0), minutes=int(m.group(3) or 0)) if m else begins + timedelta(hours=1)
            else:
                ends = begins + (timedelta(days=1) if all_day else timedelta(hours=1))
        except (ValueError, TypeError):
            skipped += 1
            continue
        length = max(ends - begins, timedelta(minutes=5))
        occurrences, ok = _expand(begins, props['RRULE'][1], limit_end) if 'RRULE' in props else ([begins], True)
        partial += not ok
        uid = props.get('UID', ('', ''))[1].strip() or hashlib.sha256(block.encode()).hexdigest()
        title = _unesc(props.get('SUMMARY', ('', 'Sans titre'))[1]).strip()[:200] or 'Sans titre'
        cats = _unesc(props.get('CATEGORIES', ('', ''))[1]).lower()
        kind = props.get('X-AXIORHUB-KIND', ('', ''))[1].strip() or ('audience' if re.search(r'audience|plaidoirie', cats + ' ' + title.lower()) else
                                                                     ('echeance' if re.search(r'[ée]ch[ée]ance|d[ée]lai', cats + ' ' + title.lower()) else 'rendez_vous'))
        matter = _unesc(props.get('X-AXIORHUB-MATTER', ('', ''))[1]).strip()
        if matter and not any(m['id'] == matter for m in load_matters(desk.c)):
            matter = ''
        for occ in occurrences:
            if occ + length < _now() - timedelta(days=366):
                continue   # passé lointain : non importé
            data = {'title': title, 'kind': kind if kind in KINDS else 'rendez_vous', 'matter': matter,
                    'location': _unesc(props.get('LOCATION', ('', ''))[1]), 'notes': _unesc(props.get('DESCRIPTION', ('', ''))[1])[:2000],
                    'reminders': [], 'all_day': all_day,
                    'start': occ.date().isoformat() if all_day else occ.isoformat(),
                    'days': max(1, length.days) if all_day else 1, 'duration_minutes': min(1440, max(5, int(length.total_seconds() // 60)))}
            try:
                create(desk, data, owner, source='import:' + label, uid=uid)
                added += 1
            except Stop as ex:
                if str(ex) == 'evenement_deja_present':
                    skipped += 1
                else:
                    skipped += 1
    desk.audit('agenda5634_import', {'label': label, 'added': added, 'skipped': skipped})
    msg = '%d événement(s) importé(s)' % added + (', %d doublon(s) ou entrée(s) ignorée(s)' % skipped if skipped else '')
    if partial:
        msg += ' ; %d série(s) récurrente(s) complexe(s) importée(s) pour leur première occurrence seulement' % partial
    return {'added': added, 'skipped': skipped, 'cancelled': cancelled, 'partial_series': partial, 'message': msg + '.'}


def import_url(desk, url, label='abonnement', owner='cabinet'):
    """Import ponctuel depuis une adresse iCal HTTPS (adresse non conservée)."""
    url = str(url or '').strip()
    if url.lower().startswith('webcal://'):
        url = 'https://' + url[9:]
    if not re.match(r'https://[^\s/]+/', url):
        raise Stop('adresse_ics_https_requise')
    from .common import HTTP
    from urllib.parse import urlsplit
    p = urlsplit(url)
    http = HTTP(p.scheme + '://' + p.netloc, timeout=30)
    raw = http.request('GET', url, limit=8_000_000)
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        text = raw.decode('latin-1')
    return import_ics(desk, text, label, owner)


# ------------------------------------------------------------------------------------------------ rappels et sauvegarde
def _backup_dir(desk):
    path = Path(desk.c['state_dir']) / 'agenda-sauvegardes'
    path.mkdir(parents=True, exist_ok=True)
    return path


def backup(desk, force=False):
    """Sauvegarde quotidienne de l'agenda local (format iCalendar, annulations comprises) ; 30 copies conservées."""
    schema(desk)
    if not desk.db.execute('SELECT 1 FROM agenda5634 LIMIT 1').fetchone():
        return None
    folder = _backup_dir(desk)
    target = folder / ('agenda-%s.ics' % _now().strftime('%Y%m%d'))
    if target.exists() and not force:
        return str(target)
    tmp = target.with_suffix('.tmp')
    tmp.write_text(export_ics(desk, include_cancelled=True), encoding='utf-8')
    tmp.replace(target)
    for old in sorted(folder.glob('agenda-*.ics'))[:-BACKUPS_KEPT]:
        try:
            old.unlink()
        except OSError:
            pass
    return str(target)


def backups(desk):
    try:
        folder = _backup_dir(desk)
    except OSError:
        return []
    return [{'name': p.name, 'size': p.stat().st_size} for p in sorted(folder.glob('agenda-*.ics'), reverse=True)]


def restore(desk, name, owner='cabinet'):
    if not re.fullmatch(r'agenda-\d{8}\.ics', str(name or '')):
        raise Stop('sauvegarde_invalide')
    path = _backup_dir(desk) / name
    if not path.is_file():
        raise Stop('sauvegarde_absente')
    return import_ics(desk, path.read_text(encoding='utf-8'), 'sauvegarde', owner)


def tick(desk, stamp=None):
    """Cycle de surveillance : rappels dus (notification dans l'application), resynchronisation, sauvegarde quotidienne."""
    schema(desk)
    now = datetime.fromtimestamp(stamp, timezone.utc) if stamp else _now()
    from .live430 import emit
    sent = 0
    rows = desk.db.execute("SELECT * FROM agenda5634 WHERE status='actif' AND starts>? AND starts<?",
                           ((now - timedelta(hours=1)).isoformat(), (now + timedelta(days=8)).isoformat())).fetchall()
    tz = _tz(desk)
    for r in rows:
        begins = datetime.fromisoformat(r['starts'])
        for minutes in json.loads(r['reminders']):
            if begins - timedelta(minutes=minutes) <= now < begins + timedelta(minutes=5):
                cur = desk.db.execute('INSERT OR IGNORE INTO agenda_rappels5634 VALUES(?,?,?)', (r['id'], minutes, now.isoformat()))
                if cur.rowcount:
                    when = begins.astimezone(tz)
                    emit(desk, 'agenda', 'Rappel : %s — %s le %s%s' % (KINDS.get(r['kind'], ''), r['title'],
                                                                     when.strftime('%d/%m/%Y'), '' if r['all_day'] else when.strftime(' à %H:%M')),
                         matter=r['matter'], dedupe='agenda5634-%s-%d' % (r['id'], minutes))
                    sent += 1
    retried = 0
    if mode(desk)['sync']:
        for r in desk.db.execute("SELECT * FROM agenda5634 WHERE sync_state='a_synchroniser' LIMIT 20").fetchall():
            state = _push(desk, dict(r))
            desk.db.execute('UPDATE agenda5634 SET sync_state=? WHERE id=?', (state, r['id']))
            retried += state == 'synchronise'
    desk.db.commit()
    saved = backup(desk)
    return {'reminders': sent, 'resynchronised': retried, 'backup': saved}


# ------------------------------------------------------------------------------------------------ API
def api(desk, route, data, method, owner='cabinet'):
    if method == 'GET':
        if route == 'agenda/liste':
            return {'mode': mode(desk), 'events': listing(desk, matter=str(data.get('matter') or ''))}
        if route == 'agenda/export':
            return {'ics': export_ics(desk)}
        if route == 'agenda/sauvegardes':
            return {'backups': backups(desk)}
        raise Stop('route_inconnue')
    if route == 'agenda/creer':
        return create(desk, data, owner)
    if route == 'agenda/modifier':
        return update(desk, data, owner)
    if route == 'agenda/annuler':
        return cancel(desk, data.get('id'))
    if route == 'agenda/importer':
        if data.get('url'):
            return import_url(desk, data.get('url'), data.get('label') or 'abonnement', owner)
        return import_ics(desk, str(data.get('ics') or ''), data.get('label') or 'fichier', owner)
    if route == 'agenda/sauvegarder':
        path = backup(desk, force=True)
        return {'message': 'Sauvegarde enregistrée.' if path else 'Agenda vide : rien à sauvegarder.', 'backups': backups(desk)}
    if route == 'agenda/restaurer':
        return restore(desk, data.get('name'), owner)
    raise Stop('route_inconnue')

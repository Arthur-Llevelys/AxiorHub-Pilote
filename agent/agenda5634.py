"""5.6.34 : agenda local du cabinet — utilisable sans aucun agenda CalDAV, la synchronisation devient une option.

- Mode par défaut : agenda LOCAL (base du poste ou du serveur) : rendez-vous, audiences, échéances et tâches datées, rattachement aux
  dossiers, rappels, sauvegarde quotidienne et export à la demande. Aucune donnée ne quitte la machine.
- Option : COPIE vers le calendrier Nextcloud de planification déjà configuré (``nextcloud_workflow``) — ce n'est pas une
  synchronisation bidirectionnelle ; un échec de copie n'empêche jamais l'enregistrement local (état « à copier », nouvel essai).
- Import ponctuel : fichier .ics (export Nextcloud, Google Agenda, Outlook…) ou adresse iCal HTTPS (adresse non conservée).
- Les événements locaux sont reflétés dans le cache d'agenda (``calendar_cache``, calendrier « local:agenda ») : la fiche d'audience,
  le briefing, l'organisation de la semaine et les alertes les voient comme les autres.

5.6.35 (audit F03–F07, F18) :
- F03 : chaque occurrence a sa propre entrée de cache (identité = identifiant interne) ; annuler une occurrence n'efface plus la série ;
  le cache « local:agenda » est reconstruit une fois à la mise à jour ;
- F04 : chaque occurrence d'une série porte une identité iCalendar stable (UID de série + RECURRENCE-ID) ; l'export donne à chaque
  occurrence un UID distinct et stable, et la série d'origine dans des propriétés X-AXIORHUB-* (réimport sans doublon) ;
- F05 : une modification partielle conserve tout ce qui n'est pas transmis (horaire, durée, journée entière, nombre de jours, notes) ;
  contrôle de version facultatif (``base``) contre l'écrasement par un autre écran ;
- F06 : EXDATE et exceptions (RECURRENCE-ID) appliquées, UNTIL inclusive, règles non prises en charge signalées, horizon annoncé ;
- F07 : « Restaurer » est distinct d'« Importer » : aperçu puis rétablissement exact (statut, rappels, dossiers, identités) à partir de
  la sauvegarde interne, après sauvegarde de l'état courant ; les rappels (VALARM) sont importés ;
- F18 : l'enregistrement local est validé AVANT toute copie réseau ; la copie se fait hors transaction et son état est écrit ensuite
  seulement si l'événement n'a pas changé entre-temps.
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
FIELDS = ('id', 'uid', 'recurrence_id', 'kind', 'title', 'description', 'location', 'starts', 'ends', 'all_day', 'matter', 'reminders', 'status',
          'source', 'sync_state', 'remote_uid', 'owner', 'created', 'updated', 'calendar_scope', 'copy_policy')
# 5.6.43 (cahier v9, § 17.3) : portée persistée de chaque événement. « personal_local » : Agenda personnel, jamais copié ;
# « legacy_bridge » : événement d'avant 5.6.43 déjà copié ou en attente de copie, à répartir par une décision explicite ;
# « copy_policy » = 'mirror' seulement quand l'avocat a choisi de garder la copie distante d'un tel événement.
FIELD_DEFAULTS = {'calendar_scope': 'personal_local', 'copy_policy': 'none'}


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
    cols = {r[1] for r in desk.db.execute('PRAGMA table_info(agenda5634)')}
    if 'recurrence_id' not in cols:   # 5.6.35 (F04) : identité de l'occurrence dans sa série
        desk.db.execute("ALTER TABLE agenda5634 ADD COLUMN recurrence_id TEXT NOT NULL DEFAULT ''")
    if 'calendar_scope' not in cols:   # 5.6.43 (§ 17.7) : migration sans copie ni suppression ; les anciennes copies sont à répartir
        desk.db.execute("ALTER TABLE agenda5634 ADD COLUMN calendar_scope TEXT NOT NULL DEFAULT 'personal_local'")
        desk.db.execute("ALTER TABLE agenda5634 ADD COLUMN copy_policy TEXT NOT NULL DEFAULT 'none'")
        desk.db.execute("UPDATE agenda5634 SET calendar_scope='legacy_bridge' WHERE remote_uid<>'' OR sync_state IN ('synchronise','a_synchroniser')")
        desk.db.execute("UPDATE agenda5634 SET sync_state='a_repartir' WHERE sync_state='a_synchroniser'")   # aucune copie relancée par la migration
        desk.db.commit()
    if not desk.db.execute("SELECT 1 FROM settings WHERE key='agenda5635_cache'").fetchone():
        rebuild_cache(desk)   # 5.6.35 (F03) : une entrée de cache par occurrence active (les autres calendriers sont conservés)
        desk.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('agenda5635_cache','\"fait\"')")
        desk.db.commit()


def mode(desk):
    """5.6.43 (§ 17.3) : l'Agenda personnel n'est JAMAIS copié vers un agenda distant, quelle que soit la configuration Nextcloud.
    ``sync`` ne concerne plus que les anciens événements dont l'avocat a choisi de garder la copie distante."""
    cfg = desk.c.get('nextcloud_workflow') or {}
    bridge = bool(cfg.get('enabled') and cfg.get('planning_calendar_url'))
    return {'local': True, 'sync': bridge, 'personal_copy': False,
            'label': 'Agenda personnel (local, non synchronisé)',
            'help': ('Vos rendez-vous, audiences et échéances sont enregistrés dans AxiorHub, sur l’instance utilisée (ce poste ou votre '
                     'serveur), sans copie vers un agenda en ligne. L’export .ics reste une action explicite ; les agendas Nextcloud, CalDAV ou '
                     'Google se consultent dans « Agenda en ligne ».')}


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


def _reminders(value, kind):
    if isinstance(value, str):
        value = [x for x in re.split(r'[,;\s]+', value) if x]
    try:
        out = sorted({int(x) for x in (value if value is not None else ([1440, 60] if kind != 'echeance' else [10080, 1440]))})
    except (TypeError, ValueError):
        raise Stop('rappel_invalide') from None
    if any(x not in REMINDERS for x in out) or len(out) > 4:
        raise Stop('rappel_invalide')
    return out


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
    # 5.6.35 (F05) : « description » est le champ canonique ; « notes » reste accepté (ancien formulaire)
    text = data['description'] if 'description' in data else data.get('notes')
    return {'title': title, 'kind': kind, 'matter': matter, 'location': str(data.get('location') or '').strip()[:200],
            'description': str(text or '').strip()[:2000], 'reminders': _reminders(data.get('reminders'), kind)}


# ------------------------------------------------------------------------------------------------ cache commun d'agenda (F03)
def _cache_id(row):
    return hashlib.sha256(('local|' + row['id']).encode()).hexdigest()


def _mirror(desk, row):
    """Reflet d'UNE occurrence dans le cache d'agenda commun ; les autres occurrences de la série ne sont jamais touchées."""
    desk.db.execute('DELETE FROM calendar_cache WHERE calendar_url=? AND id=?', (LOCAL_URL, _cache_id(row)))
    if row['status'] != 'actif':
        return
    label = KINDS.get(row['kind'], '')
    title = row['title'] if row['kind'] in ('rendez_vous', 'autre') else '%s : %s' % (label, row['title'])
    desk.db.execute('INSERT OR REPLACE INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (_cache_id(row), row['uid'], row.get('recurrence_id') or '', LOCAL_URL, '', '', title, row['description'], row['location'],
                     row['starts'], row['ends'], 0 if row['kind'] == 'echeance' else 1, row['matter'],
                     json.dumps([row['matter']] if row['matter'] else []), desk.now()))


def rebuild_cache(desk):
    """Reconstruit les entrées « local:agenda » depuis tous les événements locaux actifs (migration 5.6.35, restauration)."""
    desk.db.execute('DELETE FROM calendar_cache WHERE calendar_url=?', (LOCAL_URL,))
    n = 0
    for r in desk.db.execute("SELECT * FROM agenda5634 WHERE status='actif'").fetchall():
        _mirror(desk, dict(r))
        n += 1
    return n


def _row(desk, ident):
    row = desk.db.execute('SELECT * FROM agenda5634 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('evenement_absent')
    return dict(row)


# ------------------------------------------------------------------------------------------------ copie Nextcloud hors transaction (F18)
def _push(desk, row):
    """Copie facultative vers le calendrier Nextcloud de planification ; appelée HORS de toute transaction d'écriture locale.
    Renvoie (état, uid distant)."""
    if not mode(desk)['sync']:
        return 'local', row.get('remote_uid') or ''
    # 5.6.43 (§ 17.3) : refus au niveau du connecteur, quel que soit l'appelant (création, modification, annulation, import,
    # restauration, reprise périodique) : seul un ancien événement dont l'avocat a gardé la copie distante peut être copié.
    if row.get('calendar_scope', 'personal_local') != 'legacy_bridge' or row.get('copy_policy') != 'mirror':
        return row.get('sync_state') if row.get('sync_state') in ('a_repartir', 'synchronise') else 'local', row.get('remote_uid') or ''
    if desk.db.in_transaction:   # garde-fou : jamais d'appel réseau pendant une écriture SQLite
        desk.db.commit()
    try:
        from .workplan import _dav
        client = _dav(desk)
        calendar = (desk.c.get('nextcloud_workflow') or {})['planning_calendar_url']
        event_id = hashlib.sha256(('agenda5634|' + row['id']).encode()).hexdigest()
        if row['status'] != 'actif':
            if row.get('remote_uid'):
                client.delete_event(calendar, row['remote_uid'], '')
            return 'synchronise', row.get('remote_uid') or ''
        uid = client.put_event(calendar, event_id, row['title'], datetime.fromisoformat(row['starts']), datetime.fromisoformat(row['ends']),
                               ('Événement de l’agenda AxiorHub.\nDossier : %s\n%s' % (row['matter'] or 'non relié', row['description']))[:2000],
                               replace=bool(row.get('remote_uid')))
        return 'synchronise', str(uid or '')
    except Exception:   # noqa: BLE001 — jamais bloquant : l'agenda local fait foi
        return 'a_synchroniser', row.get('remote_uid') or ''


def _copy(desk, row):
    """Copie réseau puis écriture courte de son état, seulement si l'événement n'a pas changé pendant l'appel."""
    state, remote = _push(desk, row)
    desk.db.execute('UPDATE agenda5634 SET sync_state=?, remote_uid=? WHERE id=? AND updated=?', (state, remote, row['id'], row['updated']))
    desk.db.commit()
    return state


def _message(state, base):
    return base + (' dans l’Agenda personnel (local, non synchronisé)' if state in ('local', 'a_repartir') else
                   (' et sa copie distante conservée mise à jour' if state == 'synchronise' else ' (copie distante conservée à reprendre)')) + '.'


# ------------------------------------------------------------------------------------------------ création, modification, annulation
def create(desk, data, owner='cabinet', source='local', uid='', recurrence_id='', ident=None, copy=True):
    schema(desk)
    clean = _clean(desk, data)
    begins, ends, all_day = _when(desk, data)
    ident = ident or uuid.uuid4().hex
    uid = uid or 'axiorhub-local-%s@agenda.local' % ident
    stamp = desk.now()
    row = {'id': ident, 'uid': uid, 'recurrence_id': recurrence_id, 'kind': clean['kind'], 'title': clean['title'], 'description': clean['description'],
           'location': clean['location'], 'starts': begins.astimezone(timezone.utc).isoformat(), 'ends': ends.astimezone(timezone.utc).isoformat(),
           'all_day': int(all_day), 'matter': clean['matter'], 'reminders': json.dumps(clean['reminders']), 'status': 'actif', 'source': source,
           'sync_state': 'local', 'remote_uid': '', 'owner': owner, 'created': stamp, 'updated': stamp,
           'calendar_scope': 'personal_local', 'copy_policy': 'none'}   # 5.6.43 : un événement créé ici n'est jamais copié
    try:
        desk.db.execute('INSERT INTO agenda5634(%s) VALUES(%s)' % (','.join(row), ','.join('?' for _ in row)), tuple(row.values()))
    except Exception as ex:   # noqa: BLE001
        if 'UNIQUE' in str(ex):
            desk.db.rollback()
            raise Stop('evenement_deja_present') from None
        raise
    _mirror(desk, row)
    desk.db.commit()   # F18 : enregistré localement AVANT toute copie réseau
    desk.audit('agenda5634_cree', {'id': ident, 'kind': row['kind'], 'matter': row['matter'], 'source': source})
    state = row['sync_state']   # 5.6.43 (§ 17.3) : « copy » n'a plus d'effet ; aucune copie réseau à la création
    return {'id': ident, 'event': public(row), 'message': _message(state, 'Événement enregistré')}


def update(desk, data, owner='cabinet'):
    """Modification partielle (F05) : un champ absent garde sa valeur ; l'horaire n'est recalculé que s'il est transmis."""
    schema(desk)
    row = _row(desk, data.get('id'))
    if row['status'] != 'actif':
        raise Stop('evenement_annule')
    if data.get('base') and str(data['base']) != row['updated']:
        raise Stop('evenement_modifie_entre_temps')
    tz = _tz(desk)
    old_start, old_end = datetime.fromisoformat(row['starts']), datetime.fromisoformat(row['ends'])
    merged = {'title': row['title'], 'kind': row['kind'], 'matter': row['matter'], 'location': row['location'], 'description': row['description'],
              'reminders': json.loads(row['reminders'])}
    for key in ('title', 'kind', 'matter', 'location', 'reminders'):
        if key in data:
            merged[key] = data[key]
    if 'description' in data or 'notes' in data:
        merged['description'] = data['description'] if 'description' in data else data['notes']
    clean = _clean(desk, merged)
    timing = any(k in data for k in ('start', 'duration_minutes', 'all_day', 'days'))
    if timing:
        all_day = data['all_day'] if 'all_day' in data else bool(row['all_day'])
        if all_day:
            start = data.get('start') or old_start.astimezone(tz).date().isoformat()
            days = data.get('days') or max(1, (old_end - old_start).days if row['all_day'] else 1)
            when = {'all_day': True, 'start': str(start)[:10], 'days': days}
        else:
            start = data.get('start') or old_start.astimezone(tz).isoformat()
            minutes = data.get('duration_minutes') or (int((old_end - old_start).total_seconds() // 60) if not row['all_day'] else 60)
            when = {'all_day': False, 'start': start, 'duration_minutes': minutes}
        begins, ends, all_day = _when(desk, when)
        starts, ends_iso = begins.astimezone(timezone.utc).isoformat(), ends.astimezone(timezone.utc).isoformat()
    else:
        starts, ends_iso, all_day = row['starts'], row['ends'], bool(row['all_day'])
    reminders = json.dumps(clean['reminders'])
    moved = starts != row['starts'] or reminders != row['reminders']
    row.update(clean)
    mirrored = mode(desk)['sync'] and row.get('calendar_scope') == 'legacy_bridge' and row.get('copy_policy') == 'mirror'
    row.update(starts=starts, ends=ends_iso, all_day=int(all_day), reminders=reminders, updated=desk.now(),
               sync_state='a_synchroniser' if mirrored else row['sync_state'])
    desk.db.execute('UPDATE agenda5634 SET kind=?,title=?,description=?,location=?,starts=?,ends=?,all_day=?,matter=?,reminders=?,sync_state=?,updated=? WHERE id=?',
                    (row['kind'], row['title'], row['description'], row['location'], row['starts'], row['ends'], row['all_day'], row['matter'],
                     row['reminders'], row['sync_state'], row['updated'], row['id']))
    if moved:   # nouvel horaire ou nouveaux rappels : rappels à nouveau dus
        desk.db.execute('DELETE FROM agenda_rappels5634 WHERE event=?', (row['id'],))
    _mirror(desk, row)
    desk.db.commit()
    desk.audit('agenda5634_modifie', {'id': row['id']})
    state = _copy(desk, row) if mirrored else row['sync_state']
    row['sync_state'] = state
    return {'id': row['id'], 'event': public(row), 'message': _message(state, 'Événement modifié')}


def cancel(desk, ident, scope='occurrence'):
    """Annule une occurrence (par défaut) ou toute la série active de même UID (``scope='serie'``)."""
    schema(desk)
    row = _row(desk, ident)
    if row['status'] == 'annule':
        return {'id': row['id'], 'message': 'Événement déjà annulé.'}
    targets = [row]
    if scope == 'serie' and row['recurrence_id']:
        targets = [dict(r) for r in desk.db.execute("SELECT * FROM agenda5634 WHERE uid=? AND status='actif'", (row['uid'],))]
    stamp = desk.now()
    for r in targets:
        r.update(status='annule', updated=stamp)
        desk.db.execute("UPDATE agenda5634 SET status='annule',updated=? WHERE id=?", (stamp, r['id']))
        _mirror(desk, r)
    desk.db.commit()
    desk.audit('agenda5634_annule', {'id': row['id'], 'portee': scope, 'nombre': len(targets)})
    if mode(desk)['sync']:
        for r in targets:
            if r.get('calendar_scope') == 'legacy_bridge' and r.get('copy_policy') == 'mirror':   # 5.6.43 : jamais un événement personnel
                _copy(desk, r)
    return {'id': row['id'], 'cancelled': len(targets),
            'message': ('Série annulée (%d occurrence(s))' % len(targets) if len(targets) > 1 else 'Événement annulé') +
                       ' ; conservé dans l’historique et les sauvegardes.'}


def public(row):
    out = {k: row[k] for k in ('id', 'kind', 'title', 'description', 'location', 'starts', 'ends', 'matter', 'status', 'source', 'sync_state')}
    out['calendar_scope'] = row.get('calendar_scope', 'personal_local')
    out['all_day'] = bool(row['all_day'])
    out['reminders'] = json.loads(row['reminders']) if isinstance(row['reminders'], str) else row['reminders']
    out['kind_label'] = KINDS.get(row['kind'], row['kind'])
    out['series'] = bool(row.get('recurrence_id'))
    out['updated'] = row.get('updated', '')
    return out


def listing(desk, start=None, end=None, matter='', limit=500):
    schema(desk)
    a = (start or (_now() - timedelta(days=1))).astimezone(timezone.utc).isoformat()
    b = (end or (_now() + timedelta(days=60))).astimezone(timezone.utc).isoformat()
    sql = "SELECT * FROM agenda5634 WHERE status='actif' AND starts<? AND ends>?" + (' AND matter=?' if matter else '') + ' ORDER BY starts LIMIT ?'
    args = (b, a) + ((matter,) if matter else ()) + (int(limit),)
    from .membres5650 import viewer_hidden
    hidden = viewer_hidden()   # 5.6.50 : événements d'un dossier réservé à d'autres membres
    return [public(dict(r)) for r in desk.db.execute(sql, args) if not (r['matter'] and r['matter'] in hidden)]


# ------------------------------------------------------------------------------------------------ iCalendar : export (F04)
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


def _export_uid(row):
    """UID d'export stable et distinct par occurrence ; la série d'origine reste dans X-AXIORHUB-SERIES."""
    if not row['recurrence_id']:
        return row['uid']
    return '%s-%s' % (row['uid'], re.sub(r'[^0-9A-Za-z]', '', row['recurrence_id']))


def export_ics(desk, include_cancelled=False):
    schema(desk)
    rows = desk.db.execute('SELECT * FROM agenda5634' + ('' if include_cancelled else " WHERE status='actif'") + ' ORDER BY starts').fetchall()
    stamp = _now().strftime('%Y%m%dT%H%M%SZ')
    tz = _tz(desk)
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//AxiorHub Pilote//Agenda local//FR', 'CALSCALE:GREGORIAN']
    for r in rows:
        a, b = datetime.fromisoformat(r['starts']), datetime.fromisoformat(r['ends'])
        lines += ['BEGIN:VEVENT', 'UID:' + _export_uid(r), 'DTSTAMP:' + stamp]
        if r['all_day']:
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
        lines += ['X-AXIORHUB-KIND:' + r['kind'], 'X-AXIORHUB-ID:' + r['id']]
        if r['recurrence_id']:
            lines += ['X-AXIORHUB-SERIES:' + _esc(r['uid']), 'X-AXIORHUB-RECURRENCE-ID:' + r['recurrence_id']]
        if r['matter']:
            lines.append('X-AXIORHUB-MATTER:' + _esc(r['matter']))
        for minutes in json.loads(r['reminders']):
            lines += ['BEGIN:VALARM', 'ACTION:DISPLAY', 'DESCRIPTION:' + _esc(r['title']), 'TRIGGER:-PT%dM' % minutes, 'END:VALARM']
        lines.append('END:VEVENT')
    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold(x) for x in lines) + '\r\n'


# ------------------------------------------------------------------------------------------------ iCalendar : import (F06, F07)
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


def _rid(dt, all_day):
    """Identité RECURRENCE-ID normalisée : date (journée entière) ou date-heure UTC."""
    return dt.strftime('%Y%m%d') if all_day else dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _dates(props_list, tz):
    out = set()
    for prop, value in props_list:
        for v in value.split(','):
            try:
                dt, all_day = _parse_dt(prop, v, tz)
            except (ValueError, TypeError):
                continue
            out.add(_rid(dt, all_day))
    return out


def _expand(begins, rule, limit_end, all_day=False):
    """Occurrences d'une règle RRULE (DAILY, WEEKLY [BYDAY], MONTHLY, YEARLY ; INTERVAL, COUNT, UNTIL inclusive) bornées à l'horizon.
    Renvoie (occurrences, prise_en_charge_complete, motif)."""
    parts = dict(p.split('=', 1) for p in rule.split(';') if '=' in p)
    freq = parts.get('FREQ', '')
    if freq not in ('DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'):
        return [begins], False, 'frequence_non_prise_en_charge'
    if any(k in parts for k in ('BYMONTHDAY', 'BYSETPOS', 'BYMONTH', 'BYHOUR', 'BYMINUTE', 'BYWEEKNO', 'BYYEARDAY')) or (parts.get('BYDAY') and freq != 'WEEKLY'):
        return [begins], False, 'regle_complexe'
    try:
        interval = int(parts.get('INTERVAL', '1') or 1)
        count = int(parts['COUNT']) if 'COUNT' in parts else None
    except ValueError:
        return [begins], False, 'regle_invalide'
    if not 1 <= interval <= 1000 or (count is not None and not 1 <= count <= 5000):
        return [begins], False, 'regle_invalide'
    until = None
    if parts.get('UNTIL'):
        u = parts['UNTIL']
        try:
            if len(u) == 8:   # DATE : le jour indiqué est inclus (RFC 5545 §3.3.10)
                until = datetime.strptime(u, '%Y%m%d').replace(tzinfo=begins.tzinfo) + timedelta(days=1) - timedelta(microseconds=1)
            elif u.endswith('Z'):
                until = datetime.strptime(u[:15], '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
            else:
                until = datetime.strptime(u[:15], '%Y%m%dT%H%M%S').replace(tzinfo=begins.tzinfo)
        except ValueError:
            return [begins], False, 'regle_invalide'
    days = [('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU').index(d[-2:]) for d in parts.get('BYDAY', '').split(',') if d[-2:] in ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')]
    out, n, k = [], 0, 0
    while len(out) < 1000 and k < 10000:
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
            if (until and occ > until) or (count is not None and n >= count):   # F06 : UNTIL inclusive
                return out, True, ''
            if occ > limit_end:
                return out, True, 'horizon'
            out.append(occ)
            n += 1
    return out, True, 'horizon'


def _alarms(block):
    """Rappels d'un VEVENT (VALARM TRIGGER relatifs avant le début) ramenés aux délais proposés ; les autres sont signalés."""
    kept, other = set(), 0
    for trigger in re.findall(r'BEGIN:VALARM.*?TRIGGER[^:]*:([^\n]+).*?END:VALARM', block, flags=re.S):
        m = re.fullmatch(r'-P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?)?', trigger.strip())
        minutes = (int(m.group(1) or 0) * 10080 + int(m.group(2) or 0) * 1440 + int(m.group(3) or 0) * 60 + int(m.group(4) or 0)) if m else None
        if minutes in REMINDERS:
            kept.add(minutes)
        else:
            other += 1
    return sorted(kept)[:4], other


def _events(raw, tz):
    events = []
    for block in re.findall(r'BEGIN:VEVENT\n(.*?)\nEND:VEVENT', raw.replace('\r', ''), flags=re.S):
        props, multi = {}, {}
        for line in re.sub(r'BEGIN:VALARM.*?END:VALARM\n?', '', block, flags=re.S).split('\n'):
            if ':' not in line:
                continue
            key, value = line.split(':', 1)
            name = key.split(';')[0].upper()
            props.setdefault(name, (key, value))
            multi.setdefault(name, []).append((key, value))
        if 'DTSTART' in props:
            events.append({'props': props, 'multi': multi, 'block': block})
    return events


def import_ics(desk, text, label='import', owner='cabinet'):
    """Import ponctuel d'un fichier iCalendar : événements ajoutés à l'agenda local ; doublons ignorés ; rien n'est envoyé ailleurs.
    Les exclusions (EXDATE) et exceptions (RECURRENCE-ID) sont appliquées ; une règle non prise en charge est signalée."""
    schema(desk)
    raw = _unfold(text)
    if 'BEGIN:VCALENDAR' not in raw.upper():
        raise Stop('fichier_ics_invalide')
    if len(raw) > 8_000_000:
        raise Stop('fichier_ics_trop_volumineux')
    tz = _tz(desk)
    label = re.sub(r'[^\w .-]', '', str(label or 'import'))[:40] or 'import'
    limit_end = _now() + timedelta(days=HORIZON_DAYS)
    added = skipped = cancelled = partial = alarms_other = 0
    reasons, horizon = set(), False
    events = _events(raw, tz)
    overrides = {}
    for ev in events:   # exceptions d'une série : remplacent l'occurrence d'origine
        if 'RECURRENCE-ID' in ev['props'] and 'RRULE' not in ev['props']:
            try:
                dt, ad = _parse_dt(*ev['props']['RECURRENCE-ID'], tz)
            except (ValueError, TypeError):
                continue
            overrides.setdefault(ev['props'].get('UID', ('', ''))[1].strip(), set()).add(_rid(dt, ad))
    for ev in events:
        props = ev['props']
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
        series_uid = _unesc(props.get('X-AXIORHUB-SERIES', ('', ''))[1]).strip()
        uid = series_uid or props.get('UID', ('', ''))[1].strip() or hashlib.sha256(ev['block'].encode()).hexdigest()
        fixed_rid = props.get('X-AXIORHUB-RECURRENCE-ID', ('', ''))[1].strip()
        if 'RRULE' in props:
            occurrences, ok, why = _expand(begins, props['RRULE'][1], limit_end, all_day)
            if not ok:
                partial += 1
                reasons.add(why)
            horizon |= why == 'horizon'
            excluded = _dates(ev['multi'].get('EXDATE', []), tz) | overrides.get(uid, set())
            if 'RDATE' in props or 'EXRULE' in props:
                partial += 1
                reasons.add('rdate_exrule_non_pris_en_charge')
            occurrences = [(o, _rid(o, all_day)) for o in occurrences if _rid(o, all_day) not in excluded]
        elif 'RECURRENCE-ID' in props:
            try:
                rdt, rad = _parse_dt(*props['RECURRENCE-ID'], tz)
                occurrences = [(begins, _rid(rdt, rad))]
            except (ValueError, TypeError):
                occurrences = [(begins, '')]
        else:
            occurrences = [(begins, fixed_rid)]
        title = _unesc(props.get('SUMMARY', ('', 'Sans titre'))[1]).strip()[:200] or 'Sans titre'
        cats = _unesc(props.get('CATEGORIES', ('', ''))[1]).lower()
        kind = props.get('X-AXIORHUB-KIND', ('', ''))[1].strip() or ('audience' if re.search(r'audience|plaidoirie', cats + ' ' + title.lower()) else
                                                                     ('echeance' if re.search(r'[ée]ch[ée]ance|d[ée]lai', cats + ' ' + title.lower()) else 'rendez_vous'))
        matter = _unesc(props.get('X-AXIORHUB-MATTER', ('', ''))[1]).strip()
        if matter and not any(m['id'] == matter for m in load_matters(desk.c)):
            matter = ''
        reminders, other = _alarms(ev['block'])
        alarms_other += other
        description = _unesc(props.get('DESCRIPTION', ('', ''))[1])
        if matter:
            description = re.sub(r'\n?Dossier : %s\s*$' % re.escape(matter), '', description)
        for occ, rid in occurrences:
            if occ + length < _now() - timedelta(days=366):
                continue   # passé lointain : non importé
            data = {'title': title, 'kind': kind if kind in KINDS else 'rendez_vous', 'matter': matter,
                    'location': _unesc(props.get('LOCATION', ('', ''))[1]), 'description': description[:2000],
                    'reminders': reminders, 'all_day': all_day,
                    'start': occ.date().isoformat() if all_day else occ.isoformat(),
                    'days': max(1, length.days) if all_day else 1, 'duration_minutes': min(1440, max(5, int(length.total_seconds() // 60)))}
            try:
                create(desk, data, owner, source='import:' + label, uid=uid, recurrence_id=rid if ('RRULE' in props or rid) else '')
                added += 1
            except Stop:
                skipped += 1
    desk.audit('agenda5634_import', {'label': label, 'added': added, 'skipped': skipped})
    msg = '%d événement(s) importé(s)' % added + (', %d doublon(s) ou entrée(s) ignorée(s)' % skipped if skipped else '')
    if partial:
        msg += ' ; %d série(s) récurrente(s) importée(s) partiellement (règle non prise en charge : %s)' % (partial, ', '.join(sorted(reasons)))
    if horizon:
        msg += ' ; séries développées jusqu’au %s' % limit_end.astimezone(tz).strftime('%d/%m/%Y')
    if alarms_other:
        msg += ' ; %d rappel(s) d’un autre type non repris' % alarms_other
    return {'added': added, 'skipped': skipped, 'cancelled': cancelled, 'partial_series': partial, 'reasons': sorted(reasons),
            'covered_until': limit_end.date().isoformat(), 'message': msg + '.'}


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


# ------------------------------------------------------------------------------------------------ sauvegarde et restauration (F07)
def _backup_dir(desk):
    path = Path(desk.c['state_dir']) / 'agenda-sauvegardes'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _snapshot(desk):
    return {'version': 2, 'saved': _now().isoformat(), 'events': [{k: r[k] for k in FIELDS} for r in desk.db.execute('SELECT * FROM agenda5634')]}


def backup(desk, force=False, name=None):
    """Sauvegarde quotidienne de l'agenda local : copie interne exacte (JSON : identités, statuts, rappels, dossiers) et export
    iCalendar lisible par d'autres logiciels ; 30 copies conservées."""
    schema(desk)
    if not desk.db.execute('SELECT 1 FROM agenda5634 LIMIT 1').fetchone():
        return None
    folder = _backup_dir(desk)
    stem = name or 'agenda-%s' % _now().strftime('%Y%m%d')
    target = folder / (stem + '.ics')
    if target.exists() and not force:
        return str(target)
    for suffix, content in (('.json', json.dumps(_snapshot(desk), ensure_ascii=False)), ('.ics', export_ics(desk, include_cancelled=True))):
        tmp = folder / (stem + suffix + '.tmp')
        tmp.write_text(content, encoding='utf-8')
        tmp.replace(folder / (stem + suffix))
    for old in sorted(folder.glob('agenda-*.ics'))[:-BACKUPS_KEPT]:
        for path in (old, old.with_suffix('.json')):
            try:
                path.unlink()
            except OSError:
                pass
    return str(target)


def backups(desk):
    try:
        folder = _backup_dir(desk)
    except OSError:
        return []
    return [{'name': p.name, 'size': p.stat().st_size, 'exacte': p.with_suffix('.json').is_file()}
            for p in sorted(folder.glob('agenda-*.ics'), reverse=True)]


def _saved_events(desk, name):
    if not re.fullmatch(r'agenda-[\w-]{8,40}\.ics', str(name or '')):
        raise Stop('sauvegarde_invalide')
    path = _backup_dir(desk) / name
    if not path.is_file():
        raise Stop('sauvegarde_absente')
    exact = path.with_suffix('.json')
    if exact.is_file():
        return json.loads(exact.read_text(encoding='utf-8'))['events'], True
    return None, False


def restore(desk, name, owner='cabinet', apply=False):
    """Restauration (F07) : aperçu par défaut (ajoutés, réactivés, remplacés, conservés) ; avec ``apply``, l'état courant est d'abord
    sauvegardé, puis la sauvegarde choisie est rétablie exactement dans une transaction courte (statut, rappels, dossier, identités).
    Une sauvegarde sans copie interne (5.6.34) ne peut être qu'importée, et ce n'est pas une restauration intégrale."""
    schema(desk)
    events, exact = _saved_events(desk, name)
    if not exact:
        if not apply:
            return {'exacte': False, 'message': 'Cette sauvegarde (antérieure à 5.6.35) ne contient pas la copie interne : elle peut seulement être '
                                                 'importée ; statuts et rappels ne seront pas rétablis.'}
        return import_ics(desk, (_backup_dir(desk) / name).read_text(encoding='utf-8'), 'sauvegarde', owner)
    current = {r['id']: dict(r) for r in desk.db.execute('SELECT * FROM agenda5634')}
    events = [{**FIELD_DEFAULTS, **ev} for ev in events]
    plan = {'ajoutes': 0, 'reactives': 0, 'remplaces': 0, 'conserves': 0}
    for ev in events:
        cur = current.get(ev['id'])
        if cur is None:
            plan['ajoutes'] += 1
        elif {k: cur[k] for k in FIELDS if k not in ('sync_state', 'remote_uid')} == {k: ev[k] for k in FIELDS if k not in ('sync_state', 'remote_uid')}:
            plan['conserves'] += 1
        elif cur['status'] != 'actif' and ev['status'] == 'actif':
            plan['reactives'] += 1
        else:
            plan['remplaces'] += 1
    if not apply:
        return {'exacte': True, 'apercu': plan, 'message': 'Aperçu : %(ajoutes)d ajouté(s), %(reactives)d réactivé(s), %(remplaces)d remplacé(s), '
                                                         '%(conserves)d inchangé(s). Rien n’est modifié avant votre confirmation.' % plan}
    safety = backup(desk, force=True, name='agenda-avant-restauration-%s' % _now().strftime('%Y%m%d%H%M%S'))
    if not desk.db.in_transaction:
        desk.db.execute('BEGIN IMMEDIATE')
    try:
        for ev in events:
            values = tuple(ev.get(k, FIELD_DEFAULTS.get(k, '')) for k in FIELDS)   # 5.6.43 : sauvegardes antérieures : portée personnelle
            desk.db.execute('INSERT OR REPLACE INTO agenda5634(%s) VALUES(%s)' % (','.join(FIELDS), ','.join('?' for _ in FIELDS)), values)
            desk.db.execute('DELETE FROM agenda_rappels5634 WHERE event=?', (ev['id'],))
        rebuild_cache(desk)
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    desk.audit('agenda5634_restauration', {'sauvegarde': name, **plan})
    return {'exacte': True, 'apercu': plan, 'sauvegarde_prealable': Path(safety).name if safety else '',
            'message': 'Agenda rétabli : %(ajoutes)d ajouté(s), %(reactives)d réactivé(s), %(remplaces)d remplacé(s). Aucun calendrier externe '
                       'n’a été modifié.' % plan}


def legacy_inventory(desk):
    """5.6.43 (§ 17.7) : anciens événements déjà copiés ou en attente de copie, à répartir (hors compteur de l'Agenda personnel)."""
    schema(desk)
    return [{**public(dict(r)), 'remote_uid': bool(r['remote_uid']), 'copy_policy': r['copy_policy']}
            for r in desk.db.execute("SELECT * FROM agenda5634 WHERE calendar_scope='legacy_bridge' AND copy_policy<>'mirror' ORDER BY starts")]


def decide_legacy(desk, ident, decision, owner='cabinet'):
    """Décision explicite pour un ancien événement : « garder_local » (devient personnel ; la copie distante existante n'est PAS supprimée)
    ou « garder_copie » (la copie distante déjà créée continue d'être tenue à jour). Aucune décision n'est prise par défaut."""
    schema(desk)
    row = _row(desk, ident)
    if row.get('calendar_scope') != 'legacy_bridge':
        raise Stop('evenement_deja_reparti')
    if decision == 'garder_local':
        desk.db.execute("UPDATE agenda5634 SET calendar_scope='personal_local',copy_policy='none',sync_state='local',updated=? WHERE id=?",
                        (desk.now(), row['id']))
        message = 'Événement gardé dans l’Agenda personnel (local, non synchronisé). La copie distante éventuelle n’a pas été supprimée.'
    elif decision == 'garder_copie':
        desk.db.execute("UPDATE agenda5634 SET copy_policy='mirror',sync_state=CASE WHEN sync_state='a_repartir' THEN 'a_synchroniser' "
                        "ELSE sync_state END,updated=? WHERE id=?", (desk.now(), row['id']))
        message = 'La copie distante de cet ancien événement sera tenue à jour.'
    else:
        raise Stop('decision_agenda_invalide')
    desk.db.commit()
    desk.audit('agenda5643_repartition', {'id': row['id'], 'decision': decision, 'owner': owner})
    return {'id': row['id'], 'decision': decision, 'message': message}


def tick(desk, stamp=None):
    """Cycle de surveillance : rappels dus (notification dans l'application), reprise des copies, sauvegarde quotidienne."""
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
    desk.db.commit()
    retried = 0
    if mode(desk)['sync']:   # 5.6.43 (§ 17.3) : seuls les anciens événements dont la copie distante a été gardée explicitement
        for r in desk.db.execute("SELECT * FROM agenda5634 WHERE sync_state='a_synchroniser' AND calendar_scope='legacy_bridge' "
                                 "AND copy_policy='mirror' LIMIT 20").fetchall():
            retried += _copy(desk, dict(r)) == 'synchronise'   # F18 : chaque copie hors transaction
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
        if route == 'agenda/a-repartir':
            return {'events': legacy_inventory(desk)}
        raise Stop('route_inconnue')
    if route == 'agenda/repartir':
        return decide_legacy(desk, data.get('id'), data.get('decision'), owner)
    if route == 'agenda/creer':
        from .visio5670 import with_link   # 5.6.70 : lien Jitsi ajouté au rendez-vous si demandé
        return create(desk, with_link(desk, data), owner)
    if route == 'agenda/modifier':
        return update(desk, data, owner)
    if route == 'agenda/annuler':
        return cancel(desk, data.get('id'), 'serie' if data.get('portee') == 'serie' else 'occurrence')
    if route == 'agenda/importer':
        if data.get('url'):
            return import_url(desk, data.get('url'), data.get('label') or 'abonnement', owner)
        return import_ics(desk, str(data.get('ics') or ''), data.get('label') or 'fichier', owner)
    if route == 'agenda/sauvegarder':
        path = backup(desk, force=True)
        return {'message': 'Sauvegarde enregistrée.' if path else 'Agenda vide : rien à sauvegarder.', 'backups': backups(desk)}
    if route == 'agenda/restaurer':
        return restore(desk, data.get('name'), owner, apply=bool(data.get('confirmer')))
    raise Stop('route_inconnue')

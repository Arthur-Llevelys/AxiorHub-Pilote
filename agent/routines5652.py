"""5.6.52 (audit 5.6.51, F10 et R01 à R09) : routines personnalisées gérées depuis Aujourd'hui.

Une routine est une **définition versionnée** (nom, résultat attendu, consigne, dossiers, calendrier, fuseau, rattrapage, portée,
propriétaire) ; chaque passage voulu est une **occurrence durable** identifiée par (routine, clé de passage) — jamais deux fois.

- Planification (R04) : le passage dû le plus récent est réservé et mis en file dans **la même transaction** (``enqueue(transactional=
  True)``) ; une file pleine laisse l'occurrence « à reprogrammer » avec son motif et son prochain essai (correction F10) ; un travail
  créé est « en file », pas terminé ; reprises bornées et progressives ; heure inexistante au changement d'heure : premier instant
  valide suivant ; heure répétée : une seule exécution ; après un arrêt, seul le passage utile le plus récent est rattrapé, dans la
  fenêtre réglée.
- Autorisations (R02) : mode « Préparation et relecture » ; aucune routine n'envoie, ne signe, ne facture ni ne prélève ; une routine
  personnelle ne lit que les dossiers encore accessibles à son auteur ; une routine du cabinet agit sous l'identité système « cabinet ».
- Pause, reprise, suppression (R05) : pause générale et pause du dossier conservent l'occurrence (« reportée : pause ») tant qu'elle
  reste le passage utile le plus récent ; la suppression annule les passages non commencés et demande l'arrêt des travaux en cours ;
  les comptes rendus et documents produits restent consultables.
- Migration (R07) : briefing, tri et bilan de la 5.2.0 deviennent des définitions stables (``r520-briefing``…), sans réactiver une
  routine désactivée ni rattraper le passé ; ``routines520.tick`` n'est plus qu'un point d'entrée vers ce planificateur unique ; les
  réglages historiques restent synchronisés dans les deux sens (une seule autorité de planification).
- Travail utile (R08) : chaque occurrence rend « produit / à décider / impossible pour telle raison / prochaine tentative ».
- Le modèle ne modifie jamais une routine : aucune exécution de code, de commande ou d'adresse arbitraire.
"""
from datetime import date, datetime, time as dtime, timedelta, timezone
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import Stop, load_matters, matter_display

SCHEMA = '''
CREATE TABLE IF NOT EXISTS routines5652(id TEXT PRIMARY KEY, owner TEXT NOT NULL, scope TEXT NOT NULL, title TEXT NOT NULL, objective TEXT NOT NULL DEFAULT '',
  instruction TEXT NOT NULL DEFAULT '', task_type TEXT NOT NULL, matters_json TEXT NOT NULL DEFAULT '[]', matter_scope TEXT NOT NULL DEFAULT '',
  schedule_json TEXT NOT NULL, timezone TEXT NOT NULL, catchup_hours INTEGER NOT NULL DEFAULT 4, max_matters INTEGER NOT NULL DEFAULT 10,
  enabled INTEGER NOT NULL DEFAULT 1, deleted_at TEXT, version INTEGER NOT NULL DEFAULT 1, permissions_json TEXT NOT NULL DEFAULT '{}',
  legacy_kind TEXT NOT NULL DEFAULT '', active_since TEXT NOT NULL, request_key TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS routines5652_request ON routines5652(owner, request_key);
CREATE TABLE IF NOT EXISTS routines5652_versions(routine_id TEXT NOT NULL, version INTEGER NOT NULL, snapshot_json TEXT NOT NULL, author TEXT NOT NULL,
  action TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(routine_id, version, action));
CREATE TABLE IF NOT EXISTS routines5652_occ(id TEXT PRIMARY KEY, routine_id TEXT NOT NULL, definition_version INTEGER NOT NULL, schedule_key TEXT NOT NULL,
  scheduled_utc TEXT NOT NULL, local_label TEXT NOT NULL, manual INTEGER NOT NULL DEFAULT 0, snapshot_json TEXT NOT NULL, state TEXT NOT NULL,
  job_id INTEGER, result_ref TEXT NOT NULL DEFAULT '', result_json TEXT NOT NULL DEFAULT '{}', error_code TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0, next_try TEXT NOT NULL DEFAULT '', reviewed INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL,
  updated TEXT NOT NULL, UNIQUE(routine_id, schedule_key));
CREATE INDEX IF NOT EXISTS routines5652_occ_state ON routines5652_occ(state, next_try);
'''
TASK_TYPES = {'briefing': 'Briefing du matin', 'tri': 'Tri des courriels', 'bilan': 'Bilan de la semaine',
              'synthese': 'Compte rendu selon la consigne', 'nouveautes': 'Point des nouveautés des dossiers',
              'mission': 'Projet préparé dans chaque dossier (mission)'}
LEGACY = ('briefing', 'tri', 'bilan')
FREQS = {'quotidien': 'Tous les jours', 'jours': 'Certains jours de la semaine', 'hebdomadaire': 'Une fois par semaine',
         'mensuel': 'Une fois par mois', 'ponctuel': 'Une seule fois'}
DAYS = (('1', 'lun.'), ('2', 'mar.'), ('3', 'mer.'), ('4', 'jeu.'), ('5', 'ven.'), ('6', 'sam.'), ('7', 'dim.'))
STATES = {'reservee': 'Réservée', 'en_file': 'En file', 'en_cours': 'En cours', 'terminee': 'Terminée', 'partielle': 'Terminée avec réserves',
          'bloquee': 'Bloquée', 'echec': 'Échec', 'a_reprogrammer': 'À reprogrammer', 'reportee_pause': 'Reportée : pause',
          'annulee': 'Annulée', 'arret_demande': 'Arrêt demandé', 'non_executee': 'Non exécutée (hors fenêtre)'}
ACTIVE = ('reservee', 'en_file', 'en_cours', 'arret_demande')
DONE = ('terminee', 'partielle', 'bloquee', 'echec', 'annulee', 'non_executee')
TEMPLATES = {
    'briefing': {'title': 'Briefing du matin', 'task_type': 'briefing', 'schedule': {'freq': 'quotidien', 'time': '07:30'}},
    'tri': {'title': 'Tri des courriels', 'task_type': 'tri', 'schedule': {'freq': 'jours', 'days': '12345', 'time': '07:00'}},
    'bilan': {'title': 'Bilan de la semaine', 'task_type': 'bilan', 'schedule': {'freq': 'hebdomadaire', 'days': '5', 'time': '17:30'}},
    'point': {'title': 'Point du matin', 'task_type': 'synthese', 'schedule': {'freq': 'jours', 'days': '12345', 'time': '08:30'},
              'instruction': 'Préparer un point des courriels reçus et des audiences des quatorze prochains jours ; produire un compte rendu.'},
    'nouveautes': {'title': 'Nouveautés des dossiers', 'task_type': 'nouveautes', 'schedule': {'freq': 'jours', 'days': '12345', 'time': '18:00'}},
    'documents': {'title': 'Documents à préparer', 'task_type': 'consultation', 'consultation': True},
}
CAPABILITIES = {'briefing': ('modele', 'agenda'), 'tri': ('modele', 'messagerie'), 'bilan': ('modele',), 'synthese': ('modele',),
                'nouveautes': (), 'mission': ('modele', 'stockage')}
CAP_LABELS = {'modele': 'Modèle d’IA', 'messagerie': 'Lecture de la messagerie', 'agenda': 'Lecture de l’agenda', 'stockage': 'Dossiers (fichiers)'}
RETRY_MINUTES = (0, 0, 0, 5, 15, 60, 60, 60)
MAX_ATTEMPTS = len(RETRY_MINUTES)
MIGRATION_VERSION = 1


def _now():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    return (dt or _now()).astimezone(timezone.utc).isoformat(timespec='seconds')


def _sha(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def cabinet_tz(desk):
    try:
        return ZoneInfo(desk.c.get('calendar', {}).get('timezone', 'Europe/Paris'))
    except ZoneInfoNotFoundError:
        return timezone.utc


def _tz(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise Stop('fuseau_invalide') from None


# ------------------------------------------------------------------------------------------------ calendrier (R04)
def normalize_schedule(raw):
    raw = raw if isinstance(raw, dict) else {}
    freq = str(raw.get('freq') or 'quotidien')
    if freq not in FREQS:
        raise Stop('frequence_routine_invalide')
    t = str(raw.get('time') or '08:00')
    if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', t):
        raise Stop('heure_routine_invalide')
    out = {'freq': freq, 'time': t}
    if freq == 'quotidien':
        out['days'] = '1234567'
    elif freq in ('jours', 'hebdomadaire'):
        days = ''.join(sorted(set(re.sub(r'[^1-7]', '', str(raw.get('days') or '')))))
        if not days or (freq == 'hebdomadaire' and len(days) != 1):
            raise Stop('jours_routine_invalides')
        out['days'] = days
    elif freq == 'mensuel':
        try:
            md = int(raw.get('monthday') or 1)
        except (TypeError, ValueError):
            raise Stop('jour_du_mois_invalide') from None
        if not 1 <= md <= 28:
            raise Stop('jour_du_mois_invalide')
        out['monthday'] = md
    else:
        d = str(raw.get('date') or '')
        try:
            date.fromisoformat(d)
        except ValueError:
            raise Stop('date_routine_invalide') from None
        out['date'] = d
    return out


def _matches(schedule, d):
    f = schedule['freq']
    if f in ('quotidien', 'jours', 'hebdomadaire'):
        return str(d.isoweekday()) in schedule.get('days', '1234567')
    if f == 'mensuel':
        return d.day == schedule['monthday']
    return d.isoformat() == schedule['date']


def local_slot(d, hhmm, tz):
    """Instant voulu le jour ``d`` à ``hhmm`` dans ``tz`` : heure inexistante (passage à l'heure d'été) → premier instant valide suivant ;
    heure répétée (passage à l'heure d'hiver) → première occurrence seulement (une seule exécution)."""
    hh, mm = (int(x) for x in hhmm.split(':'))
    naive = datetime.combine(d, dtime(hh, mm))
    aware = naive.replace(tzinfo=tz, fold=0)
    back = aware.astimezone(timezone.utc).astimezone(tz)
    if back.replace(tzinfo=None) != naive:          # heure inexistante : on avance minute par minute jusqu'à un instant valide
        probe = naive
        for _ in range(240):
            probe += timedelta(minutes=1)
            cand = probe.replace(tzinfo=tz, fold=0)
            if cand.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == probe:
                return cand, 'heure inexistante (changement d’heure) : passage à %s' % probe.strftime('%H:%M')
    return aware, ''


def upcoming(schedule, tzname, start=None, count=3):
    """Prochains passages (heure locale et UTC), sans effet : sert à l'aperçu et à la carte."""
    schedule = normalize_schedule(schedule)
    tz = _tz(tzname)
    start = (start or _now()).astimezone(tz)
    out = []
    d = start.date()
    for _ in range(800):
        if _matches(schedule, d):
            slot, note = local_slot(d, schedule['time'], tz)
            if slot >= start:
                out.append({'local': slot.strftime('%Y-%m-%d %H:%M'), 'label': slot.strftime('%d/%m/%Y à %H:%M'), 'utc': _iso(slot), 'jour': d.isoformat(),
                            'note': note})
                if len(out) >= count:
                    break
        d += timedelta(days=1)
        if schedule['freq'] == 'ponctuel' and d > date.fromisoformat(schedule['date']):
            break
    return out


def _due(defn, now):
    """Passage dû le plus récent (clé, instant UTC, libellé local) dans la fenêtre de rattrapage, ou None."""
    schedule = json.loads(defn['schedule_json'])
    tz = _tz(defn['timezone'])
    local_now = now.astimezone(tz)
    since = datetime.fromisoformat(defn['active_since'])
    window = timedelta(hours=max(0, int(defn['catchup_hours'])))
    for back in range(0, 40):
        d = local_now.date() - timedelta(days=back)
        if not _matches(schedule, d):
            continue
        slot, note = local_slot(d, schedule['time'], tz)
        if slot > local_now:
            continue
        if slot < since or local_now - slot > window:
            return None
        return {'key': '%s %s' % (d.isoformat(), schedule['time']), 'utc': _iso(slot), 'label': slot.strftime('%d/%m/%Y %H:%M'), 'note': note}
    return None


# ------------------------------------------------------------------------------------------------ définitions (R03)
def _snapshot(row):
    keys = ('id', 'title', 'objective', 'instruction', 'task_type', 'matters_json', 'schedule_json', 'timezone', 'catchup_hours', 'max_matters',
            'enabled', 'permissions_json', 'owner', 'scope', 'version')
    return {k: row[k] for k in keys}


def _log_version(desk, row, author, action):
    desk.db.execute('INSERT OR REPLACE INTO routines5652_versions(routine_id,version,snapshot_json,author,action,at) VALUES(?,?,?,?,?,?)',
                    (row['id'], row['version'], json.dumps(_snapshot(row), ensure_ascii=False), author, action, _iso()))


def get(desk, rid):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM routines5652 WHERE id=?', (str(rid or ''),)).fetchone()
    if not row or row['deleted_at']:
        raise Stop('routine_absente')
    return row


def _hidden_for(desk, owner):
    """Dossiers réservés à d'autres pour l'auteur d'une routine personnelle (son rôle est lu dans l'annuaire des membres)."""
    if owner == 'cabinet':
        return frozenset()
    from . import membres5650
    role = 'avocat'
    try:
        row = desk.db.execute('SELECT role FROM membres5650 WHERE owner=?', (owner,)).fetchone()
        role = row['role'] if row else role
    except Exception:   # noqa: BLE001
        pass
    try:
        return membres5650.hidden_for(desk.c['state_dir'], owner, role)
    except Stop:
        return frozenset(m['id'] for m in load_matters(desk.c, unfiltered=True))


def authorize(row, actor, role, write=False):
    """R02 / leçon F01 : objet résolu sur le serveur ; une routine personnelle n'est visible et modifiable que par son auteur (et lisible
    par l'administrateur) ; une routine du cabinet est modifiable par un avocat ou l'administrateur."""
    if row['scope'] == 'personnel' and row['owner'] != actor:
        if role != 'administrateur' or write:
            raise Stop('routine_absente')
    if write and row['scope'] == 'cabinet' and role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')


def _clean(desk, actor, data, hidden=frozenset(), legacy=''):
    title = re.sub(r'\s+', ' ', str(data.get('title') or data.get('nom') or '')).strip()[:120]
    if len(title) < 2:
        raise Stop('nom_routine_requis')
    task = str(data.get('task_type') or data.get('resultat') or 'synthese')
    if task not in TASK_TYPES:
        raise Stop('type_routine_invalide')
    instruction = re.sub(r'[ \t]+', ' ', str(data.get('instruction') or data.get('consigne') or '')).strip()[:4000]
    if task in ('synthese', 'mission') and len(instruction) < 5:
        raise Stop('consigne_routine_requise')
    matters = data.get('matters') or data.get('dossiers') or []
    if isinstance(matters, str):
        matters = [m for m in re.split(r'[\n,;]+', matters) if m.strip()]
    known = {m['id'] for m in load_matters(desk.c, unfiltered=True)}
    matters = sorted({str(m).strip() for m in matters if str(m).strip()})
    if any(m not in known for m in matters):
        raise Stop('dossier_absent')
    if any(m in hidden for m in matters):
        raise Stop('dossier_reserve')
    if task == 'mission' and not matters:
        raise Stop('dossiers_routine_requis')
    schedule = normalize_schedule(data.get('schedule') if isinstance(data.get('schedule'), dict) else {
        'freq': data.get('freq'), 'days': data.get('days') or ''.join(k for k, _ in DAYS if data.get('day_' + k) in (True, 'true', 'on')),
        'time': data.get('time') or data.get('heure'), 'monthday': data.get('monthday'), 'date': data.get('date')})
    tzname = str(data.get('timezone') or '') or None
    try:
        catchup = max(0, min(int(data.get('catchup_hours') if data.get('catchup_hours') not in (None, '') else 4), 72))
        max_matters = max(1, min(int(data.get('max_matters') or 10), 50))
    except (TypeError, ValueError):
        raise Stop('limites_routine_invalides') from None
    scope = str(data.get('scope') or 'personnel')
    if scope not in ('personnel', 'cabinet'):
        raise Stop('portee_routine_invalide')
    return {'title': title, 'objective': re.sub(r'\s+', ' ', str(data.get('objective') or data.get('objectif') or '')).strip()[:240],
            'instruction': instruction, 'task_type': task, 'matters_json': json.dumps(matters), 'matter_scope': ','.join(matters),
            'schedule_json': json.dumps(schedule, sort_keys=True), 'timezone': tzname, 'catchup_hours': catchup, 'max_matters': max_matters,
            'scope': scope, 'permissions_json': json.dumps({'mode': 'preparation', 'effets_externes': False})}


def create(desk, actor, role, data):
    """Création idempotente (clé de demande) ; aucune routine n'est créée par le modèle."""
    ensure_schema(desk)
    migrate(desk)
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    key = str(data.get('request_key') or '')
    if key and not re.fullmatch(r'[A-Za-z0-9-]{8,80}', key):
        raise Stop('identifiant_requete_invalide')
    if key:
        old = desk.db.execute('SELECT * FROM routines5652 WHERE owner=? AND request_key=?', (actor, key)).fetchone()
        if old:
            return dict(old)
    fields = _clean(desk, actor, data, _hidden_for(desk, actor))
    if fields['scope'] == 'cabinet' and role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    owner = 'cabinet' if fields['scope'] == 'cabinet' else actor
    rid = 'r52-' + _sha(actor, key or _iso(), fields['title'], _now().timestamp())[:20]
    now = _iso()
    fields['timezone'] = fields['timezone'] or str(cabinet_tz(desk))
    _tz(fields['timezone'])
    enabled = 0 if str(data.get('enabled', 'true')).lower() in ('false', '0', 'off', 'non') else 1
    desk.db.execute('INSERT INTO routines5652(id,owner,scope,title,objective,instruction,task_type,matters_json,matter_scope,schedule_json,timezone,catchup_hours,'
                    'max_matters,enabled,version,permissions_json,active_since,request_key,created_at,updated_at,updated_by) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?)',
                    (rid, owner, fields['scope'], fields['title'], fields['objective'], fields['instruction'], fields['task_type'], fields['matters_json'],
                     fields['matter_scope'], fields['schedule_json'], fields['timezone'], fields['catchup_hours'], fields['max_matters'], enabled,
                     fields['permissions_json'], now, key or None, now, now, actor))
    row = get(desk, rid)
    _log_version(desk, row, actor, 'creation')
    desk.db.commit()
    desk.audit('routine5652_creee', {'routine': rid, 'type': fields['task_type'], 'scope': fields['scope']})
    return dict(row)


def update(desk, actor, role, rid, data, version):
    """Modification sur la version affichée (écriture conditionnelle ; 409 si un autre utilisateur a modifié entre-temps). Les passages
    non commencés de l'ancienne version sont annulés et seront replanifiés sur la nouvelle ; un passage commencé garde son instantané."""
    ensure_schema(desk)
    row = get(desk, rid)
    authorize(row, actor, role, write=True)
    form = _as_form(row)
    if any(k in data for k in ('freq', 'time', 'days', 'monthday', 'date', 'schedule')) or any(str(k).startswith('day_') for k in data):
        old = form.pop('schedule')                       # calendrier modifié : les champs saisis remplacent l'ancien calendrier
        form.update({'freq': old.get('freq'), 'time': old.get('time'), 'monthday': old.get('monthday'), 'date': old.get('date')})
        if not any(str(k).startswith('day_') for k in data) and 'days' not in data:
            form['days'] = old.get('days', '')
    fields = _clean(desk, actor, {**form, **data}, _hidden_for(desk, row['owner'] if row['scope'] == 'personnel' else 'cabinet'),
                    legacy=row['legacy_kind'])
    if row['legacy_kind']:
        fields['task_type'] = row['legacy_kind']          # les routines historiques gardent leur traitement
        fields['scope'] = 'cabinet'
    fields['timezone'] = fields['timezone'] or row['timezone']
    _tz(fields['timezone'])
    enabled = row['enabled'] if 'enabled' not in data else (0 if str(data['enabled']).lower() in ('false', '0', 'off', 'non') else 1)
    now = _iso()
    cur = desk.db.execute('UPDATE routines5652 SET title=?,objective=?,instruction=?,task_type=?,matters_json=?,matter_scope=?,schedule_json=?,timezone=?,'
                          'catchup_hours=?,max_matters=?,enabled=?,scope=?,version=version+1,updated_at=?,updated_by=?,'
                          'active_since=CASE WHEN ?=1 AND enabled=0 THEN ? ELSE active_since END WHERE id=? AND version=? AND deleted_at IS NULL',
                          (fields['title'], fields['objective'], fields['instruction'], fields['task_type'], fields['matters_json'], fields['matter_scope'],
                           fields['schedule_json'], fields['timezone'], fields['catchup_hours'], fields['max_matters'], enabled, fields['scope'], now, actor,
                           enabled, now, rid, int(version)))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('version_en_conflit')
    desk.db.execute("UPDATE routines5652_occ SET state='annulee',error_code='remplacee_par_nouvelle_version',updated=? WHERE routine_id=? "
                    "AND state IN ('reservee','a_reprogrammer','reportee_pause') AND job_id IS NULL", (now, rid))
    new = get(desk, rid)
    _log_version(desk, new, actor, 'modification')
    desk.db.commit()
    if new['legacy_kind']:
        _write_legacy(desk)
    return dict(new)


def _as_form(row):
    return {'title': row['title'], 'objective': row['objective'], 'instruction': row['instruction'], 'task_type': row['task_type'],
            'matters': json.loads(row['matters_json']), 'schedule': json.loads(row['schedule_json']), 'timezone': row['timezone'],
            'catchup_hours': row['catchup_hours'], 'max_matters': row['max_matters'], 'scope': row['scope'], 'template': row['legacy_kind']}


def set_enabled(desk, actor, role, rid, enabled, version):
    return update(desk, actor, role, rid, {'enabled': 'true' if enabled else 'false'}, version)


def duplicate(desk, actor, role, rid):
    row = get(desk, rid)
    authorize(row, actor, role)
    form = _as_form(row)
    form.update({'title': ('Copie de ' + row['title'])[:120], 'enabled': 'false', 'scope': 'personnel' if role != 'administrateur' else row['scope']})
    if row['legacy_kind']:
        form['template'] = row['legacy_kind']
    return create(desk, actor, role, form)


def delete(desk, actor, role, rid, version):
    """Suppression logique : passages non commencés annulés, travaux en cours arrêtés à l'étape suivante ; résultats conservés."""
    row = get(desk, rid)
    authorize(row, actor, role, write=True)
    if row['legacy_kind']:
        raise Stop('routine_historique_desactivable')   # briefing, tri, bilan : désactivables, jamais supprimés (compatibilité)
    now = _iso()
    cur = desk.db.execute('UPDATE routines5652 SET deleted_at=?,enabled=0,version=version+1,updated_at=?,updated_by=? WHERE id=? AND version=? AND deleted_at IS NULL',
                          (now, now, actor, rid, int(version)))
    if not cur.rowcount:
        desk.db.rollback()
        raise Stop('version_en_conflit')
    stopping = 0
    for occ in desk.db.execute("SELECT id,state,job_id FROM routines5652_occ WHERE routine_id=? AND state IN ('reservee','en_file','en_cours','a_reprogrammer',"
                               "'reportee_pause')", (rid,)).fetchall():
        if occ['state'] == 'en_cours':
            desk.db.execute("UPDATE routines5652_occ SET state='arret_demande',updated=? WHERE id=?", (now, occ['id']))
            stopping += 1
        else:
            desk.db.execute("UPDATE routines5652_occ SET state='annulee',error_code='routine_supprimee',updated=? WHERE id=?", (now, occ['id']))
        if occ['job_id']:
            try:
                desk.cancel_job(occ['job_id'])
            except Exception:   # noqa: BLE001 — travail déjà terminé
                pass
    desk.db.execute('INSERT OR REPLACE INTO routines5652_versions(routine_id,version,snapshot_json,author,action,at) VALUES(?,?,?,?,?,?)',
                    (rid, row['version'] + 1, json.dumps(_snapshot(row), ensure_ascii=False), actor, 'suppression', now))
    desk.db.commit()
    desk.audit('routine5652_supprimee', {'routine': rid, 'arrets': stopping})
    return {'id': rid, 'arret_demande': stopping}


def concerned_jobs(desk, rid):
    return [dict(r) for r in desk.db.execute("SELECT id,state,local_label FROM routines5652_occ WHERE routine_id=? AND state IN ('reservee','en_file','en_cours',"
                                             "'a_reprogrammer','reportee_pause')", (rid,))]


# ------------------------------------------------------------------------------------------------ migration (R07)
def _legacy_blob(desk):
    from . import routines520
    raw = desk.settings('routines520', {}) or {}
    s = routines520.settings(desk)
    return raw, {k: {'enabled': bool(s[k]['enabled']), 'time': s[k]['time'], 'days': s[k]['days']} for k in LEGACY}


def migrate(desk):
    """Migration relançable : sauvegarde des réglages historiques, définitions stables r520-*, état d'activation conservé, aucun rattrapage."""
    ensure_schema(desk)
    raw, legacy = _legacy_blob(desk)
    blob_hash = _sha(json.dumps(raw, sort_keys=True, default=str))
    done = int(desk.settings('routines5652:migration', 0) or 0)
    if done >= MIGRATION_VERSION and desk.settings('routines5652:legacy_hash', '') == blob_hash:
        return False
    if done < MIGRATION_VERSION and not desk.settings('routines5652:backup520', None):
        desk.setting('routines5652:backup520', {'settings': raw, 'since': desk.settings('routines520:since', ''), 'at': _iso()})
    since = desk.settings('routines520:since', '') or _iso()
    now = _iso()
    tzname = str(cabinet_tz(desk))
    for kind in LEGACY:
        cfg = legacy[kind]
        sched = {'freq': 'quotidien' if cfg['days'] == '1234567' else 'jours', 'days': cfg['days'], 'time': cfg['time']}
        rid = 'r520-' + kind
        row = desk.db.execute('SELECT * FROM routines5652 WHERE id=?', (rid,)).fetchone()
        if row is None:
            desk.db.execute('INSERT INTO routines5652(id,owner,scope,title,objective,task_type,schedule_json,timezone,catchup_hours,enabled,version,'
                            'permissions_json,legacy_kind,active_since,created_at,updated_at,updated_by) VALUES(?,?,?,?,?,?,?,?,4,?,1,?,?,?,?,?,?)',
                            (rid, 'cabinet', 'cabinet', TASK_TYPES[kind], {'briefing': 'Audiences, échéances et courriels du jour',
                                                                       'tri': 'Courriels de la nuit classés, réponses urgentes en brouillon',
                                                                       'bilan': 'Ce qui a été fait et ce qui reste'}[kind], kind,
                             json.dumps(normalize_schedule(sched), sort_keys=True), tzname, 1 if cfg['enabled'] else 0,
                             json.dumps({'mode': 'preparation', 'effets_externes': False}), kind, since, now, now, 'migration'))
            _log_version(desk, desk.db.execute('SELECT * FROM routines5652 WHERE id=?', (rid,)).fetchone(), 'migration', 'migration')
        else:
            new_sched = json.dumps(normalize_schedule(sched), sort_keys=True)
            enabled = 1 if cfg['enabled'] else 0
            if new_sched != row['schedule_json'] or enabled != row['enabled']:   # réglages historiques modifiés ailleurs : reportés
                desk.db.execute('UPDATE routines5652 SET schedule_json=?,enabled=?,version=version+1,updated_at=?,updated_by=?,'
                                'active_since=CASE WHEN ?=1 AND enabled=0 THEN ? ELSE active_since END WHERE id=?',
                                (new_sched, enabled, now, 'reglages', enabled, now, rid))
                _log_version(desk, desk.db.execute('SELECT * FROM routines5652 WHERE id=?', (rid,)).fetchone(), 'reglages', 'synchronisation')
    desk.db.commit()
    desk.setting('routines5652:migration', MIGRATION_VERSION)
    desk.setting('routines5652:legacy_hash', blob_hash)
    return True


def _write_legacy(desk):
    """Une routine historique modifiée depuis Aujourd'hui met à jour les réglages historiques (mêmes réglages partout)."""
    raw = dict(desk.settings('routines520', {}) or {})
    for kind in LEGACY:
        row = desk.db.execute('SELECT * FROM routines5652 WHERE id=?', ('r520-' + kind,)).fetchone()
        if not row:
            continue
        sched = json.loads(row['schedule_json'])
        cur = dict(raw.get(kind) or {})
        cur.update({'enabled': bool(row['enabled']), 'time': sched['time'], 'days': sched.get('days', '1234567')})
        raw[kind] = cur
    desk.setting('routines520', raw)
    desk.setting('routines5652:legacy_hash', _sha(json.dumps(desk.settings('routines520', {}) or {}, sort_keys=True, default=str)))


# ------------------------------------------------------------------------------------------------ planification (R04, R05, F10)
def _globally_paused(desk):
    return bool(desk.settings('missions567:pause', False))


def _matters_paused(desk, matters):
    if not matters:
        return False
    from .pause5629 import is_paused
    return all(is_paused(desk, m) for m in matters)


def _job_for(row):
    if row['task_type'] in LEGACY:   # briefing, tri, bilan : même traitement que la 5.2.0, rattaché à l'occurrence
        return 'routine520', {'kind': row['task_type']}
    return 'routine5652', {}


def _reconcile(desk):
    """Occurrences dont le travail est terminé, en erreur ou annulé hors du planificateur : état rapproché (jamais « terminé » sans résultat)."""
    for occ in desk.db.execute("SELECT o.id,o.state,o.job_id,j.status,j.result FROM routines5652_occ o JOIN jobs j ON j.id=o.job_id "
                               "WHERE o.state IN ('en_file','en_cours','arret_demande')").fetchall():
        status = occ['status']
        if status in ('pending', 'running', 'cancel_requested', 'suspended'):
            continue
        state = {'done': None, 'error': 'echec', 'cancelled': 'annulee', 'failed': 'echec'}.get(status, None)
        if status == 'done' and occ['state'] != 'arret_demande':
            row = desk.db.execute('SELECT result_json FROM routines5652_occ WHERE id=?', (occ['id'],)).fetchone()
            if row and row['result_json'] not in ('', '{}'):
                continue                                    # le traitement a lui-même enregistré son résultat
            state = 'terminee'
        if state is None:
            state = 'annulee' if occ['state'] == 'arret_demande' else 'echec'
        desk.db.execute('UPDATE routines5652_occ SET state=?,updated=? WHERE id=?', (state, _iso(), occ['id']))
    desk.db.commit()


def _enqueue_occurrence(desk, row, occ_id, manual=False):
    """Réservation déjà écrite ; mise en file dans la même transaction. Renvoie (job_id, erreur)."""
    kind, args = _job_for(row)
    args = dict(args, occurrence=occ_id)
    if row['task_type'] in LEGACY and not manual:
        args['planned'] = desk.db.execute('SELECT schedule_key FROM routines5652_occ WHERE id=?', (occ_id,)).fetchone()[0][:10]
    from .arret5643 import automatic
    job = None
    try:
        with automatic(desk):
            job = desk.enqueue(kind, args, priority=0 if manual else 30, transactional=True)
    except Stop as ex:
        return None, str(ex).split(':')[0]
    return job, ''


def tick(desk, now=None):
    """Planificateur unique : renvoie les routines mises en file (types historiques par leur nom, autres par identifiant). Une file pleine
    laisse l'occurrence à reprogrammer et l'erreur est remontée à l'appelant après le passage complet (aucune perte silencieuse)."""
    ensure_schema(desk)
    now = (now or _now()).astimezone(timezone.utc)
    if not desk.settings('routines520:since', ''):
        # Premier passage (installation, mise à niveau) : rien n'est rattrapé pour la journée en cours.
        desk.setting('routines520:since', now.astimezone(cabinet_tz(desk)).isoformat())
        migrate(desk)
        return []
    migrate(desk)
    _reconcile(desk)
    if not _globally_paused(desk):   # R05 / leçon F08 : un passage reporté pour pause reprend s'il reste le passage utile le plus récent
        resume_paused(desk, now)
    queued, errors = [], []
    order = {'tri': 0, 'briefing': 1, 'bilan': 2}
    rows = sorted(desk.db.execute('SELECT * FROM routines5652 WHERE deleted_at IS NULL AND enabled=1').fetchall(),
                  key=lambda r: (order.get(r['legacy_kind'], 9), r['created_at']))
    for row in rows:
        due = _due(row, now)
        if not due:
            continue
        occ = desk.db.execute('SELECT * FROM routines5652_occ WHERE routine_id=? AND schedule_key=?', (row['id'], due['key'])).fetchone()
        if occ and occ['state'] in DONE + ACTIVE:
            continue
        if occ and occ['state'] == 'a_reprogrammer' and occ['next_try'] and occ['next_try'] > _iso(now):
            continue
        if occ and occ['attempts'] >= MAX_ATTEMPTS:
            desk.db.execute("UPDATE routines5652_occ SET state='echec',updated=? WHERE id=?", (_iso(now), occ['id']))
            desk.db.commit()
            continue
        matters = json.loads(row['matters_json'])
        paused = _globally_paused(desk) or _matters_paused(desk, matters)
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.execute('BEGIN IMMEDIATE')
        try:
            occ_id = occ['id'] if occ else 'occ-' + _sha(row['id'], due['key'])[:24]
            if not occ:
                desk.db.execute('INSERT INTO routines5652_occ(id,routine_id,definition_version,schedule_key,scheduled_utc,local_label,snapshot_json,state,created,updated) '
                                'VALUES(?,?,?,?,?,?,?,?,?,?)', (occ_id, row['id'], row['version'], due['key'], due['utc'], due['label'],
                                                                json.dumps(_snapshot(row), ensure_ascii=False), 'reservee', _iso(now), _iso(now)))
            if paused:
                desk.db.execute("UPDATE routines5652_occ SET state='reportee_pause',error_code=?,updated=? WHERE id=?",
                                ('pause_generale' if _globally_paused(desk) else 'pause_dossier', _iso(now), occ_id))
                desk.db.commit()
                continue
            job, err = _enqueue_occurrence(desk, row, occ_id)
            if job:
                desk.db.execute("UPDATE routines5652_occ SET state='en_file',job_id=?,error_code='',attempts=attempts+1,updated=? WHERE id=?", (job, _iso(now), occ_id))
                if row['legacy_kind']:
                    desk.setting('routines520:last:' + row['legacy_kind'], due['key'][:10])
            else:
                attempts = (occ['attempts'] if occ else 0) + 1
                wait = RETRY_MINUTES[min(attempts - 1, len(RETRY_MINUTES) - 1)]
                desk.db.execute("UPDATE routines5652_occ SET state='a_reprogrammer',error_code=?,attempts=?,next_try=?,updated=? WHERE id=?",
                                (err, attempts, _iso(now + timedelta(minutes=wait)), _iso(now), occ_id))
                errors.append(err)
            desk.db.commit()
        except BaseException:
            desk.db.rollback()
            raise
        if job:
            try:
                desk.notify_queued(job, {})
            except Exception:   # noqa: BLE001 — le travail est enregistré ; le worker le prendra au prochain passage
                pass
            queued.append(row['legacy_kind'] or row['id'])
    if errors:
        raise Stop(errors[0])
    return queued


def resume_paused(desk, now=None):
    """Après une reprise : les passages reportés pour pause redeviennent éligibles s'ils restent le passage utile le plus récent."""
    ensure_schema(desk)
    now = now or _now()
    for occ in desk.db.execute("SELECT o.id,o.routine_id,o.schedule_key FROM routines5652_occ o WHERE o.state='reportee_pause'").fetchall():
        row = desk.db.execute('SELECT * FROM routines5652 WHERE id=? AND deleted_at IS NULL AND enabled=1', (occ['routine_id'],)).fetchone()
        due = _due(row, now) if row else None
        if row and due and due['key'] == occ['schedule_key']:
            desk.db.execute("UPDATE routines5652_occ SET state='a_reprogrammer',next_try='',updated=? WHERE id=?", (_iso(now), occ['id']))
        else:
            desk.db.execute("UPDATE routines5652_occ SET state='non_executee',error_code='pause_au_dela_du_passage',updated=? WHERE id=?",
                            (_iso(now), occ['id']))
    desk.db.commit()


def run_now(desk, actor, role, rid, click_key=''):
    """Lancement demandé : occurrence manuelle identifiable, idempotente (même clic : même occurrence), distincte de la récurrence."""
    ensure_schema(desk)
    migrate(desk)
    row = get(desk, rid)
    authorize(row, actor, role)
    key = 'manuel:' + (click_key if re.fullmatch(r'[A-Za-z0-9-]{8,80}', str(click_key or '')) else _now().strftime('%Y-%m-%dT%H:%M'))
    occ = desk.db.execute('SELECT * FROM routines5652_occ WHERE routine_id=? AND schedule_key=?', (rid, key)).fetchone()
    if occ and occ['state'] not in ('a_reprogrammer',):
        return dict(occ)
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        occ_id = occ['id'] if occ else 'occ-' + _sha(rid, key)[:24]
        if not occ:
            desk.db.execute('INSERT INTO routines5652_occ(id,routine_id,definition_version,schedule_key,scheduled_utc,local_label,manual,snapshot_json,state,created,updated) '
                            'VALUES(?,?,?,?,?,?,1,?,?,?,?)', (occ_id, rid, row['version'], key, _iso(), _now().astimezone(cabinet_tz(desk)).strftime('%d/%m/%Y %H:%M'),
                                                             json.dumps(_snapshot(row), ensure_ascii=False), 'reservee', _iso(), _iso()))
        job, err = _enqueue_occurrence(desk, row, occ_id, manual=True)
        if not job:
            desk.db.execute("UPDATE routines5652_occ SET state='a_reprogrammer',error_code=?,attempts=attempts+1,updated=? WHERE id=?", (err, _iso(), occ_id))
            desk.db.commit()
            raise Stop(err)
        desk.db.execute("UPDATE routines5652_occ SET state='en_file',job_id=?,attempts=attempts+1,updated=? WHERE id=?", (job, _iso(), occ_id))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    try:
        desk.notify_queued(job, {})
    except Exception:   # noqa: BLE001
        pass
    return dict(desk.db.execute('SELECT * FROM routines5652_occ WHERE id=?', (occ_id,)).fetchone())


# ------------------------------------------------------------------------------------------------ exécution (R08)
def _occurrence(desk, oid):
    ensure_schema(desk)
    occ = desk.db.execute('SELECT * FROM routines5652_occ WHERE id=?', (str(oid or ''),)).fetchone()
    if not occ:
        raise Stop('occurrence_absente')
    return occ


def start_occurrence(desk, oid):
    """Début d'exécution : refus si la routine a été supprimée ou l'arrêt demandé (contrôle avant toute étape)."""
    if not oid:
        return None
    occ = _occurrence(desk, oid)
    if occ['state'] in ('annulee', 'arret_demande'):
        desk.db.execute("UPDATE routines5652_occ SET state='annulee',updated=? WHERE id=?", (_iso(), oid))
        desk.db.commit()
        raise Stop('routine_arretee')
    desk.db.execute("UPDATE routines5652_occ SET state='en_cours',updated=? WHERE id=? AND state IN ('reservee','en_file','a_reprogrammer')", (_iso(), oid))
    desk.db.commit()
    return occ


def checkpoint(desk, oid):
    if oid and desk.db.execute("SELECT 1 FROM routines5652_occ WHERE id=? AND state='arret_demande'", (oid,)).fetchone():
        raise Stop('routine_arretee')


def finish(desk, oid, state, result, ref=''):
    if not oid:
        return
    desk.db.execute('UPDATE routines5652_occ SET state=?,result_json=?,result_ref=?,updated=? WHERE id=?',
                    (state, json.dumps(result, ensure_ascii=False, default=str)[:20000], str(ref)[:200], _iso(), oid))
    desk.db.commit()


def capabilities(desk, task_type):
    have = set()
    c = desk.c
    if c.get('ollama') or c.get('ai_providers'):
        have.add('modele')
    if (c.get('mail') or {}).get('host'):
        have.add('messagerie')
    if (c.get('calendar') or {}).get('urls'):
        have.add('agenda')
    if (c.get('nextcloud') or {}).get('roots'):
        have.add('stockage')
    need = CAPABILITIES.get(task_type, ())
    return {'requises': list(need), 'manquantes': [x for x in need if x not in have]}


def execute(desk, args):
    """Travail « routine5652 » : synthèse, nouveautés ou missions ; résultat structuré, jamais d'effet externe."""
    oid = str(args.get('occurrence') or '')
    occ = start_occurrence(desk, oid)
    snap = json.loads(occ['snapshot_json'])
    task = snap['task_type']
    owner = snap['owner']
    hidden = _hidden_for(desk, owner) if snap['scope'] == 'personnel' else frozenset()
    from .pause5629 import is_paused
    wanted = json.loads(snap['matters_json'])
    matters = [m for m in wanted if m not in hidden and not is_paused(desk, m)][:int(snap.get('max_matters') or 10)]
    result = {'produit': [], 'a_decider': [], 'impossible': [], 'prochaine': ''}
    for m in wanted:
        if m in hidden:
            result['impossible'].append('dossier %s : accès retiré à l’auteur de la routine' % m)
        elif is_paused(desk, m):
            result['impossible'].append('dossier %s : IA en pause sur ce dossier' % m)
    caps = capabilities(desk, task)
    if 'modele' in caps['manquantes']:
        result['impossible'].append('aucun modèle d’IA configuré')
        finish(desk, oid, 'bloquee', result)
        return {'occurrence': oid, 'state': 'bloquee'}
    try:
        if task == 'nouveautes':
            ref = _run_news(desk, oid, snap, matters, result, owner)
        elif task == 'mission':
            ref = _run_missions(desk, oid, snap, matters, result, owner)
        elif task == 'synthese':
            ref = _run_synthesis(desk, oid, snap, matters, result)
        else:
            raise Stop('type_routine_invalide')
    except Stop as ex:
        if str(ex) == 'routine_arretee':
            finish(desk, oid, 'annulee', result)
            return {'occurrence': oid, 'state': 'annulee'}
        result['impossible'].append('traitement interrompu : %s' % str(ex).split(':')[0])
        finish(desk, oid, 'echec' if not result['produit'] else 'partielle', result)
        raise
    state = 'partielle' if result['impossible'] else 'terminee'
    finish(desk, oid, state, result, ref)
    return {'occurrence': oid, 'state': state, 'resultat': result}


def _period_since(desk, routine_id, oid):
    row = desk.db.execute("SELECT updated FROM routines5652_occ WHERE routine_id=? AND id<>? AND state IN ('terminee','partielle') ORDER BY updated DESC LIMIT 1",
                          (routine_id, oid)).fetchone()
    return datetime.fromisoformat(row['updated']) if row else _now() - timedelta(days=1)


def _run_news(desk, oid, snap, matters, result, owner):
    from . import memoire5651, modele5651, routines520
    since = _period_since(desk, snap['id'], oid)
    targets = matters or [m['id'] for m in load_matters(desk.c)][:int(snap.get('max_matters') or 10)]
    lines = []
    for mid in targets:
        checkpoint(desk, oid)
        st = modele5651.ensure_current(desk, mid)
        if st.get('state') != 'ok':
            result['impossible'].append('dossier %s : données non actualisées (%s)' % (mid, st.get('error', '')))
        news = [c for c in memoire5651.changes(desk, mid, 0, False, 100) if c['created'] >= _iso(since)]
        if not news:
            continue
        m = next((x for x in load_matters(desk.c) if x['id'] == mid), {'id': mid})
        lines.append('## %s' % matter_display(m))
        lines += ['- %s' % c['title'] for c in news[:20]]
        result['produit'].append('%s : %d nouveauté(s)' % (matter_display(m), len(news)))
    text = '\n'.join(lines) or 'Aucune nouveauté dans les dossiers suivis depuis le dernier passage.'
    rid = routines520._store(desk, 'r52:' + snap['id'], _now().date().isoformat(), '# ' + snap['title'] + '\n\n' + text, {'matters': targets},
                             result['impossible'])
    return rid


def _run_missions(desk, oid, snap, matters, result, owner):
    from . import missions5651
    actor = owner if snap['scope'] == 'personnel' else 'cabinet'
    ids = []
    for mid in matters:
        checkpoint(desk, oid)
        try:
            m = missions5651.start(desk, actor, {'matter': mid, 'objectif': 'autre', 'consigne': snap['instruction'],
                                                 'click_key': ('rt-' + _sha(oid, mid))[:40]})
            ids.append(m['id'])
            result['produit'].append('dossier %s : mission %s' % (mid, 'rejointe' if m.get('rejointe') else 'lancée'))
            result['a_decider'].append({'dossier': mid, 'mission': m['id'], 'action': 'Relire le projet quand il est prêt'})
        except Stop as ex:
            result['impossible'].append('dossier %s : %s' % (mid, str(ex).split(':')[0]))
    return ','.join(ids)[:200]


def _run_synthesis(desk, oid, snap, matters, result):
    from . import routines520
    rid_routine = desk.db.execute('SELECT routine_id FROM routines5652_occ WHERE id=?', (oid,)).fetchone()[0]
    since = _period_since(desk, rid_routine, oid)
    data = {'consigne_de_la_routine': snap['instruction'], 'periode_depuis': _iso(since)}
    try:
        data['courriels'] = [{k: x.get(k) for k in ('from', 'subject', 'date', 'summary', 'snippet') if x.get(k)}
                             for x in routines520.mails(desk, since, 120, 20)][:80]
    except Exception as ex:   # noqa: BLE001 — la messagerie manque : le reste avance, la limite est indiquée
        result['impossible'].append('courriels non lus : %s' % (str(ex).split(':')[0] or type(ex).__name__))
    try:
        today = _now().astimezone(cabinet_tz(desk)).date()
        data['agenda'] = routines520.calendar(desk, today, today + timedelta(days=14))[:80]
    except Exception as ex:   # noqa: BLE001
        result['impossible'].append('agenda non lu : %s' % (str(ex).split(':')[0] or type(ex).__name__))
    if matters:
        from .contexte5651 import for_drafting
        data['dossiers'] = {}
        for mid in matters:
            checkpoint(desk, oid)
            data['dossiers'][mid] = for_drafting(desk, mid, snap['instruction'], snap['owner'], budget=4000)
    checkpoint(desk, oid)
    lang = routines520.langue(desk)
    text = routines520.ask(desk, 'Exécute la consigne de la routine « %s » à partir des seules données fournies ; rédige un compte rendu en '
                                 'Markdown avec des titres courts ; indique ce qui est à décider et ce qui n’a pas pu être vérifié. N’envoie rien.'
                           % snap['title'], data, 3500, lang)
    report = routines520._store(desk, 'r52:' + rid_routine, _now().date().isoformat(), text, {'routine': rid_routine}, result['impossible'])
    result['produit'].append('compte rendu « %s »' % snap['title'])
    return report


# ------------------------------------------------------------------------------------------------ lecture (R06)
def listing(desk, actor, role, limit=50, offset=0):
    ensure_schema(desk)
    migrate(desk)
    rows = [r for r in desk.db.execute('SELECT * FROM routines5652 WHERE deleted_at IS NULL ORDER BY CASE WHEN legacy_kind<>\'\' THEN 0 ELSE 1 END, created_at')
            if r['scope'] == 'cabinet' or r['owner'] == actor or role == 'administrateur']
    out = []
    for r in rows[offset:offset + limit]:
        d = dict(r)
        d['schedule'] = json.loads(r['schedule_json'])
        d['matters'] = json.loads(r['matters_json'])
        try:
            d['next'] = upcoming(d['schedule'], r['timezone'], count=3) if r['enabled'] else []
        except Stop:
            d['next'] = []
        last = desk.db.execute('SELECT * FROM routines5652_occ WHERE routine_id=? ORDER BY updated DESC LIMIT 1', (r['id'],)).fetchone()
        d['last'] = dict(last) if last else None
        if d['last']:
            d['last']['result'] = json.loads(last['result_json'] or '{}')
        d['capabilities'] = capabilities(desk, r['task_type'])
        d['editable'] = (r['scope'] == 'cabinet' and role in ('administrateur', 'avocat')) or r['owner'] == actor
        out.append(d)
    return {'routines': out, 'total': len(rows)}


def runs(desk, actor, role, rid, limit=30):
    row = get(desk, rid)
    authorize(row, actor, role)
    out = []
    for o in desk.db.execute('SELECT * FROM routines5652_occ WHERE routine_id=? ORDER BY created DESC LIMIT ?', (rid, int(limit))):
        d = dict(o)
        d['result'] = json.loads(o['result_json'] or '{}')
        d['state_label'] = STATES.get(o['state'], o['state'])
        if o['error_code']:
            from .erreurs5634 import explain
            d['error_message'] = explain(o['error_code'])
        out.append(d)
    return out


def queue_items(desk, prefix='', actor='cabinet', role='administrateur'):
    """Résultats à relire dans la file commune (A06) : un élément par compte rendu non relu, clé unique par occurrence."""
    ensure_schema(desk)
    items = []
    for o in desk.db.execute("SELECT o.*,r.title,r.owner,r.scope FROM routines5652_occ o JOIN routines5652 r ON r.id=o.routine_id "
                             "WHERE o.state IN ('terminee','partielle','bloquee','echec') AND o.reviewed=0 AND r.legacy_kind='' "
                             "ORDER BY o.updated DESC LIMIT 30"):
        if o['scope'] == 'personnel' and o['owner'] != actor and role != 'administrateur':
            continue
        res = json.loads(o['result_json'] or '{}')
        items.append({'class': 'livrable', 'object': 'routine:' + o['id'], 'revision': o['updated'], 'matter': '', 'matter_name': 'Cabinet',
                      'title': 'Routine « %s » (%s) : %s' % (o['title'], o['local_label'], STATES.get(o['state'], o['state']).lower()),
                      'consequence': '; '.join((res.get('impossible') or [])[:2]) or 'Compte rendu disponible dans Aujourd’hui.',
                      'href': prefix + '/aujourdhui#r52-' + o['routine_id'], 'choices': ('relu', 'reporter'), 'occurrence': o['id']})
    return items

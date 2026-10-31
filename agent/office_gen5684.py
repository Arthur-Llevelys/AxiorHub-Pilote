"""5.6.84 : génération de coédition et sauvegarde prouvée (cahier 5.6.84, §§ 2, 3 et 16).

Avant : la clé de l'éditeur dépendait du chemin et de l'ETag. Après un enregistrement forcé, un second onglet recevait une autre clé pendant que la
première session restait ouverte, donc deux salles d'édition pour un même document. Le verrou Python ne protégeait qu'un processus, et les statuts 3 et 7
étaient acquittés sans laisser de trace pour l'interface.

Ici : un identifiant de document stable (il survit à un renommage), une seule génération ouverte ou en clôture par document, une clé fixe pendant toute la
génération, un bail en base entre processus, et un reçu de sauvegarde persistant. L'acquittement d'un callback n'est jamais une preuve : « Enregistré » n'existe
qu'après relecture du fichier distant et comparaison de son empreinte."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from .common import Stop

IDLE_HOURS = 12            # sans callback ni présence, la génération est considérée comme abandonnée par le serveur bureautique
LEASE_SECONDS = 90
WAIT_SECONDS = 30
MAX_ATTEMPTS = 3
OPEN_STATES = ('open', 'closing')
KEEP_DAYS = 14             # copies de travail d'une sauvegarde en échec, texte observé d'une génération close : pas de conservation au-delà
FINAL_SAVES = ('verified', 'conflict_copy_verified', 'superseded')
SAVE_LABELS = {'pending': 'Enregistrement en attente', 'fetching': 'Enregistrement en cours', 'writing': 'Enregistrement en cours', 'verified': 'Enregistré',
               'conflict_copy_verified': 'Conflit : copie enregistrée', 'failed': 'Échec de l’enregistrement', 'superseded': 'Version plus récente déjà enregistrée'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS office_documents5684(document_id TEXT PRIMARY KEY, path TEXT NOT NULL, fileid TEXT NOT NULL DEFAULT '', matter TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS office_documents5684_path ON office_documents5684(path);
CREATE INDEX IF NOT EXISTS office_documents5684_fileid ON office_documents5684(fileid);
CREATE TABLE IF NOT EXISTS office_generations5684(generation_id TEXT PRIMARY KEY, document_id TEXT NOT NULL, doc_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL, base_etag TEXT NOT NULL,
    current_etag TEXT NOT NULL DEFAULT '', last_verified_hash TEXT NOT NULL DEFAULT '', last_save_at TEXT NOT NULL DEFAULT '', conflict_path TEXT NOT NULL DEFAULT '',
    participants TEXT NOT NULL DEFAULT '[]', opened TEXT NOT NULL, updated TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS office_generations5684_active ON office_generations5684(document_id) WHERE state IN ('open','closing');
CREATE TABLE IF NOT EXISTS office_saves5684(save_id TEXT PRIMARY KEY, generation_id TEXT NOT NULL, document_id TEXT NOT NULL, status INTEGER NOT NULL, state TEXT NOT NULL,
    expected_etag TEXT NOT NULL DEFAULT '', saved_path TEXT NOT NULL DEFAULT '', verified_hash TEXT NOT NULL DEFAULT '', etag TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS office_saves5684_document ON office_saves5684(document_id, created);
CREATE TABLE IF NOT EXISTS office_leases5684(document_id TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
'''


def schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _begin(desk):
    if not desk.db.in_transaction:
        desk.db.execute('BEGIN IMMEDIATE')


def document_for(desk, path, info, matter=''):
    """Identifiant interne stable : retrouvé par l'identifiant Nextcloud, donc conservé après un renommage. Le chemin n'est qu'un emplacement."""
    fileid = str(info.get('fileid') or '')
    row = desk.db.execute('SELECT * FROM office_documents5684 WHERE fileid=? AND fileid<>""', (fileid,)).fetchone() if fileid else None
    row = row or desk.db.execute('SELECT * FROM office_documents5684 WHERE path=? ORDER BY created LIMIT 1', (path,)).fetchone()
    now = desk.now()
    if row:
        if row['path'] != path or (fileid and row['fileid'] != fileid) or (matter and row['matter'] != matter):
            desk.db.execute('UPDATE office_documents5684 SET path=?, fileid=?, matter=?, updated=? WHERE document_id=?', (path, fileid or row['fileid'], matter or row['matter'], now, row['document_id']))
        return row['document_id']
    document_id = uuid.uuid4().hex
    desk.db.execute('INSERT INTO office_documents5684 VALUES(?,?,?,?,?,?)', (document_id, path, fileid, matter, now, now))
    return document_id


def _age_hours(stamp):
    try:
        value = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - value).total_seconds() / 3600
    except ValueError:
        return 1e9


def open_generation(desk, path, info, matter=''):
    """Rejoint la génération en cours, quel que soit l'ETag du moment ; n'en crée une nouvelle qu'après clôture ou abandon constaté de la précédente."""
    schema(desk)
    _begin(desk)
    try:
        document_id = document_for(desk, path, info, matter)
        row = desk.db.execute("SELECT * FROM office_generations5684 WHERE document_id=? AND state IN ('open','closing')", (document_id,)).fetchone()
        if row and _age_hours(row['updated']) > IDLE_HOURS:
            desk.db.execute("UPDATE office_generations5684 SET state='expired', updated=? WHERE generation_id=?", (desk.now(), row['generation_id']))
            row = None
        if not row:
            now = desk.now()
            legacy = hashlib.sha256((path + '|' + str(info['etag'])).encode()).hexdigest()[:40]      # session ouverte avant la mise à jour : elle finit son cycle sous sa clé
            old = desk.db.execute('SELECT updated FROM office_sessions440 WHERE doc_key=?', (legacy,)).fetchone()
            free = not desk.db.execute('SELECT 1 FROM office_generations5684 WHERE doc_key=?', (legacy,)).fetchone()
            key = legacy if old and free and _age_hours(old['updated']) < IDLE_HOURS else uuid.uuid4().hex
            generation = uuid.uuid4().hex
            desk.db.execute('INSERT INTO office_generations5684(generation_id,document_id,doc_key,state,base_etag,current_etag,opened,updated) VALUES(?,?,?,?,?,?,?,?)',
                            (generation, document_id, key, 'open', info['etag'], info['etag'], now, now))
            row = desk.db.execute('SELECT * FROM office_generations5684 WHERE generation_id=?', (generation,)).fetchone()
        desk.db.commit()
    except Exception:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    return dict(row)


def by_key(desk, key):
    schema(desk)
    row = desk.db.execute('SELECT * FROM office_generations5684 WHERE doc_key=?', (key,)).fetchone()
    return dict(row) if row else None


def active(desk, document_id):
    schema(desk)
    row = desk.db.execute("SELECT * FROM office_generations5684 WHERE document_id=? AND state IN ('open','closing')", (document_id,)).fetchone()
    return dict(row) if row and _age_hours(row['updated']) <= IDLE_HOURS else None


def touch(desk, generation_id, users=None):
    desk.db.execute('UPDATE office_generations5684 SET participants=?, updated=? WHERE generation_id=?', (json.dumps([str(u)[:160] for u in (users or [])][:50]), desk.now(), generation_id))
    desk.db.commit()


def close(desk, generation_id, state='closed'):
    desk.db.execute('UPDATE office_generations5684 SET state=?, updated=? WHERE generation_id=?', (state, desk.now(), generation_id))
    desk.db.commit()


@contextmanager
def lease(desk, document_id, seconds=LEASE_SECONDS, wait=WAIT_SECONDS):
    """Coordination entre travailleurs : un bail en base, repris s'il a expiré (processus arrêté en pleine écriture)."""
    owner = uuid.uuid4().hex
    deadline = time.monotonic() + wait
    while True:
        try:
            desk.db.execute('BEGIN IMMEDIATE')
            row = desk.db.execute('SELECT expires FROM office_leases5684 WHERE document_id=?', (document_id,)).fetchone()
            if not row or row['expires'] < time.time():
                desk.db.execute('INSERT OR REPLACE INTO office_leases5684 VALUES(?,?,?)', (document_id, owner, time.time() + seconds))
                desk.db.commit()
                break
            desk.db.rollback()
        except sqlite3.OperationalError:
            if desk.db.in_transaction:
                desk.db.rollback()
        if time.monotonic() > deadline:
            raise Stop('document_occupe')
        time.sleep(0.1)
    try:
        yield owner
    finally:
        desk.db.execute('DELETE FROM office_leases5684 WHERE document_id=? AND owner=?', (document_id, owner))
        desk.db.commit()


def _blob(desk, save_id):
    return Path(desk.c['state_dir']) / 'office5684' / (save_id + '.bin')


def _stage(desk, save_id, data):
    path = _blob(desk, save_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _set(desk, save_id, state, **fields):
    names = ''.join(', %s=?' % k for k in fields)
    desk.db.execute('UPDATE office_saves5684 SET state=?, updated=?%s WHERE save_id=?' % names, (state, desk.now(), *fields.values(), save_id))
    desk.db.commit()


def save_id_of(generation_id, status, lastsave, url):
    return hashlib.sha256('|'.join((generation_id, str(status), str(lastsave or url))).encode()).hexdigest()[:32]


def record_failure(desk, gen, status):
    """Statuts 3 et 7 : l'acquittement reste celui du protocole, l'échec est durable et visible."""
    sid = hashlib.sha256(('%s|erreur|%s|%s' % (gen['generation_id'], status, gen['updated'])).encode()).hexdigest()[:32]
    desk.db.execute('INSERT OR IGNORE INTO office_saves5684(save_id,generation_id,document_id,status,state,expected_etag,error,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                    (sid, gen['generation_id'], gen['document_id'], int(status), 'failed', gen['current_etag'], 'statut_editeur_%d' % int(status), desk.now(), desk.now()))
    desk.db.commit()
    return sid


def save(desk, gen, status, lastsave, url, fetch, write):
    """Une sauvegarde : reçu persistant d'abord, puis lecture, écriture avec précondition et relecture. Un doublon vise le même reçu ; une version plus
    ancienne ne remplace jamais une plus récente. ``write(data, force_copy, copy_path)`` rend {'outcome', 'path', 'sha256', 'etag'}."""
    sid = save_id_of(gen['generation_id'], status, lastsave, url)
    now = desk.now()
    desk.db.execute('INSERT OR IGNORE INTO office_saves5684(save_id,generation_id,document_id,status,state,expected_etag,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                    (sid, gen['generation_id'], gen['document_id'], int(status), 'pending', gen['current_etag'], now, now))
    desk.db.commit()
    row = desk.db.execute('SELECT * FROM office_saves5684 WHERE save_id=?', (sid,)).fetchone()
    if row['state'] in FINAL_SAVES:
        return dict(row)
    if row['attempts'] >= MAX_ATTEMPTS:
        raise Stop('sauvegarde_echecs_repetes')
    with lease(desk, gen['document_id']):
        fresh = dict(desk.db.execute('SELECT * FROM office_generations5684 WHERE generation_id=?', (gen['generation_id'],)).fetchone())
        if lastsave and fresh['last_save_at'] and str(lastsave) < fresh['last_save_at']:
            _set(desk, sid, 'superseded')
            return dict(desk.db.execute('SELECT * FROM office_saves5684 WHERE save_id=?', (sid,)).fetchone())
        try:
            _set(desk, sid, 'fetching', attempts=row['attempts'] + 1)
            blob = _blob(desk, sid)
            data = blob.read_bytes() if blob.is_file() else fetch()
            _stage(desk, sid, data)
            _set(desk, sid, 'writing')
            newer = desk.db.execute("SELECT 1 FROM office_generations5684 WHERE document_id=? AND generation_id<>? AND opened>=? AND state<>'expired' LIMIT 1",
                                    (gen['document_id'], gen['generation_id'], fresh['opened'])).fetchone()
            out = write(data, bool(newer) or fresh['state'] in ('closed', 'expired'), fresh['conflict_path'])
        except Stop as ex:
            _set(desk, sid, 'failed', error=str(ex)[:160])
            raise
        return _finish(desk, sid, fresh, lastsave, out)


def _finish(desk, sid, gen, lastsave, out):
    copied = out['outcome'] == 'copy'
    _set(desk, sid, 'conflict_copy_verified' if copied else 'verified', saved_path=out['path'], verified_hash=out.get('sha256', ''), etag=out.get('etag', ''), error='')
    desk.db.execute('UPDATE office_generations5684 SET current_etag=?, last_verified_hash=?, last_save_at=?, conflict_path=?, updated=? WHERE generation_id=?',
                    (gen['current_etag'] if copied else out.get('etag', gen['current_etag']), out.get('sha256', gen['last_verified_hash']), str(lastsave or gen['last_save_at']),
                     out['path'] if copied else gen['conflict_path'], desk.now(), gen['generation_id']))
    desk.db.commit()
    try:
        _blob(desk, sid).unlink()
    except OSError:
        pass
    return dict(desk.db.execute('SELECT * FROM office_saves5684 WHERE save_id=?', (sid,)).fetchone())


def resume(desk, write, client, older_seconds=60):
    """Reprise après un redémarrage : le contenu distant est rapproché d'abord, pour ne pas publier deux fois."""
    schema(desk)
    limit = (datetime.now(timezone.utc) - timedelta(seconds=older_seconds)).isoformat()
    done = 0
    for row in desk.db.execute("SELECT * FROM office_saves5684 WHERE state IN ('pending','fetching','writing') AND updated<?", (limit,)).fetchall():
        blob = _blob(desk, row['save_id'])
        gen = desk.db.execute('SELECT * FROM office_generations5684 WHERE generation_id=?', (row['generation_id'],)).fetchone()
        doc = desk.db.execute('SELECT path FROM office_documents5684 WHERE document_id=?', (row['document_id'],)).fetchone()
        if not gen or not doc or not blob.is_file():
            _set(desk, row['save_id'], 'failed', error='contenu_non_conserve')
            continue
        data = blob.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        with lease(desk, row['document_id']):
            try:
                info = client.stat(doc['path'])
                if hashlib.sha256(client.download(info)).hexdigest() == sha:
                    out = {'outcome': 'replaced', 'path': doc['path'], 'sha256': sha, 'etag': info['etag']}
                else:
                    out = write(dict(gen), data)
            except Stop as ex:      # une reprise impossible reste visible, sans bloquer les autres documents
                _set(desk, row['save_id'], 'failed', error=str(ex)[:160])
                continue
            _finish(desk, row['save_id'], dict(gen), '', out)
            done += 1
    return done


def purge(desk, keep_days=KEEP_DAYS):
    """Rétention : le texte du document n'a pas à rester dans le miroir après la clôture ; le fichier, ses versions et l'historique des commandes restent. Une sauvegarde en échec garde sa copie le temps d'être reprise."""
    schema(desk)
    from .office_bridge5684 import schema as bridge_schema
    bridge_schema(desk)
    limit = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
    closed = "SELECT generation_id FROM office_generations5684 WHERE state IN ('closed','expired') AND updated<?"
    gone = desk.db.execute('DELETE FROM office_blocks5684 WHERE generation_id IN (%s)' % closed, (limit,)).rowcount
    desk.db.execute('DELETE FROM office_observations5684 WHERE generation_id IN (%s)' % closed, (limit,))
    desk.db.execute('DELETE FROM office_events5684 WHERE generation_id IN (%s)' % closed, (limit,))
    desk.db.commit()
    removed = 0
    for row in desk.db.execute("SELECT save_id FROM office_saves5684 WHERE state='failed' AND updated<?", (limit,)).fetchall():
        try:
            _blob(desk, row['save_id']).unlink()
            removed += 1
        except OSError:
            pass
    return {'blocs': gone, 'copies': removed}


def save_state(desk, document_id):
    """État consultable : ce qu'il faut afficher, sans jamais déduire « Enregistré » d'un acquittement."""
    schema(desk)
    doc = desk.db.execute('SELECT * FROM office_documents5684 WHERE document_id=?', (document_id,)).fetchone()
    if not doc:
        raise Stop('document_inconnu')
    gen = active(desk, document_id)
    row = desk.db.execute("SELECT * FROM office_saves5684 WHERE document_id=? AND state<>'superseded' ORDER BY updated DESC, rowid DESC LIMIT 1", (document_id,)).fetchone()
    out = {'document_id': document_id, 'path': doc['path'], 'generation_id': gen['generation_id'] if gen else '', 'conflict_path': gen['conflict_path'] if gen else '',
           'state': row['state'] if row else '', 'label': SAVE_LABELS.get(row['state'], '') if row else 'Aucun enregistrement depuis l’ouverture'}
    if row:
        out.update(save_id=row['save_id'], error=row['error'], sha256=row['verified_hash'], etag=row['etag'], saved_path=row['saved_path'], at=row['updated'])
    return out

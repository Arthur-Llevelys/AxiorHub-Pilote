"""5.6.88 : copie d'un stockage vers un autre.

Règles tenues par construction :

- la source est enveloppée dans ``SourceOnly`` : aucune méthode d'écriture n'y est atteignable, donc aucune suppression ni modification ;
- chaque fichier copié est relu à destination et comparé par SHA-256 ; une différence est signalée, jamais tenue pour bonne ;
- un fichier déjà présent à destination n'est jamais remplacé : même empreinte, il compte comme identique ; empreinte différente, il est signalé ;
- la copie reprend où elle s'était arrêtée (état en base, fichier par fichier) ; une panne du service met la copie en pause sans rien perdre ;
- une copie terminée ne change pas le stockage en service : ce module n'appelle jamais ``cloud_gestion5688.activate``.

Le contrat ``DAV`` échange des octets en mémoire : chaque fichier est borné (``MAX_BYTES``, réglable) et un fichier plus gros est listé à part.
Les dossiers exclus partout (« secrets », noms commençant par un point) ne sont pas copiés. Les dossiers vides ne sont pas recréés.
"""
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import PurePosixPath
import time
import uuid

from .common import Stop, clean_path
from . import cloud_gestion5688 as gestion

MAX_BYTES = 200_000_000
BUDGET = 25
MAX_ATTEMPTS = 3
LEASE = 900
KEEP = 5                # copies terminées gardées en détail
PAUSING = ('connexion_a_autoriser', 'stockage_limite_debit', 'connexion_http_indisponible', 'delai_http_depasse', 'http_401', 'http_403', 'http_507')
PROBLEMS = ('conflit', 'trop_volumineux', 'fichier_vide', 'echec', 'empreinte_differente', 'source_modifiee')
LABELS = {'a_copier': 'À copier', 'a_reprendre': 'À reprendre', 'copie': 'Copié et vérifié', 'identique': 'Déjà présent, identique', 'conflit': 'Déjà présent, contenu différent (non remplacé)',
          'trop_volumineux': 'Trop volumineux pour la copie', 'fichier_vide': 'Fichier vide (non copié)', 'echec': 'Échec', 'empreinte_differente': 'Empreinte différente après copie',
          'source_modifiee': 'Modifié à la source pendant la copie'}
STATES = {'en_cours': 'En cours', 'en_pause': 'En pause', 'terminee': 'Terminée'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS cloud_migrations5688(id TEXT PRIMARY KEY, source TEXT NOT NULL, destination TEXT NOT NULL, state TEXT NOT NULL, scan_json TEXT NOT NULL DEFAULT '{}',
  created_by TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '', lease REAL NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cloud_migration_items5688(migration TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0, etag TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
  sha256 TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL, PRIMARY KEY(migration, path));
CREATE INDEX IF NOT EXISTS cloud_migration_items5688_state ON cloud_migration_items5688(migration, state);
'''


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


class SourceOnly:
    """La source d'une copie ne peut qu'être lue : tout le reste est refusé, y compris une méthode ajoutée plus tard au contrat."""
    READS = ('inventory_step', 'stat', 'download', 'list_folder')

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        if name not in self.READS:
            raise Stop('source_en_lecture_seule')
        return getattr(self._client, name)


def _row(desk, ident):
    schema(desk)
    row = desk.db.execute('SELECT * FROM cloud_migrations5688 WHERE id=?', (str(ident or ''),)).fetchone()
    if not row:
        raise Stop('copie_absente')
    return row


def start(desk, actor, role, source, destination):
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    schema(desk)
    from . import cloud5688 as core
    if source == destination or destination not in ('nextcloud', *core.PROVIDERS) or source not in ('nextcloud', *core.PROVIDERS):
        raise Stop('copie_source_destination_invalides')
    if desk.db.execute("SELECT 1 FROM cloud_migrations5688 WHERE state IN ('en_cours','en_pause') AND source=? AND destination=?", (source, destination)).fetchone():
        raise Stop('copie_deja_en_cours')
    ident, now = uuid.uuid4().hex, _now()
    roots = [clean_path(r) for r in desk.c['nextcloud']['roots']]
    for old in desk.db.execute("SELECT id FROM cloud_migrations5688 WHERE state='terminee' ORDER BY created DESC LIMIT -1 OFFSET ?", (KEEP,)).fetchall():      # rétention : le bilan reste au journal
        desk.db.execute('DELETE FROM cloud_migration_items5688 WHERE migration=?', (old['id'],))
        desk.db.execute('DELETE FROM cloud_migrations5688 WHERE id=?', (old['id'],))
    desk.db.execute('INSERT INTO cloud_migrations5688(id,source,destination,state,scan_json,created_by,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                    (ident, source, destination, 'en_cours', json.dumps({'roots': roots, 'current': None}), str(actor), now, now))
    desk.db.commit()
    desk.audit('cloud5688_copie_lancee', {'migration': ident, 'source': source, 'destination': destination, 'par': str(actor)})
    desk.enqueue('migration5688', {'migration': ident})
    return {'id': ident, 'message': 'Copie lancée. Le stockage en service ne change pas.'}


def resume(desk, actor, role, ident):
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    row = _row(desk, ident)
    if row['state'] == 'en_cours':
        raise Stop('copie_deja_en_cours')
    desk.db.execute("UPDATE cloud_migration_items5688 SET state='a_reprendre',attempts=0,error='',updated=? WHERE migration=? AND state IN ('echec','a_reprendre','source_modifiee')",
                    (_now(), row['id']))
    desk.db.execute("UPDATE cloud_migrations5688 SET state='en_cours',last_error='',lease=0,updated=? WHERE id=?", (_now(), row['id']))
    desk.db.commit()
    desk.audit('cloud5688_copie_reprise', {'migration': row['id'], 'par': str(actor)})
    desk.enqueue('migration5688', {'migration': row['id']})
    return {'message': 'Copie reprise là où elle s’était arrêtée.'}


def pause(desk, actor, role, ident):
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    row = _row(desk, ident)
    desk.db.execute("UPDATE cloud_migrations5688 SET state='en_pause',updated=? WHERE id=? AND state='en_cours'", (_now(), row['id']))
    desk.db.commit()
    return {'message': 'Copie mise en pause.'}


def _scan(desk, row, source):
    """Un pas d'inventaire : les fichiers vont en base au fil de l'eau, l'état conservé reste petit. Renvoie vrai quand tout est relevé."""
    scan = json.loads(row['scan_json'] or '{}')
    if not scan.get('roots') and not scan.get('current'):
        return True
    root = scan['current']['root'] if scan.get('current') else scan['roots'][0]
    state = scan['current'] if scan.get('current') else None
    try:
        state, done = source.inventory_step(root, state)
    except Stop as ex:
        if str(ex) not in ('http_404', 'fichier_nextcloud_introuvable'):
            raise
        state, done = {'files': {}}, True                   # racine absente de la source : rien à copier
    now = _now()
    for path, item in (state.get('files') or {}).items():
        desk.db.execute("INSERT OR IGNORE INTO cloud_migration_items5688(migration,path,size,etag,state,updated) VALUES(?,?,?,?,'a_copier',?)",
                        (row['id'], path, int(item.get('size') or 0), str(item.get('etag') or ''), now))
    if done:
        scan['current'] = None
        scan['roots'] = [r for r in scan.get('roots') or [] if r != root]
    else:
        scan['current'] = {**state, 'files': {}, 'visited': 0}
    desk.db.execute('UPDATE cloud_migrations5688 SET scan_json=?,updated=? WHERE id=?', (json.dumps(scan), now, row['id']))
    desk.db.commit()
    return not scan['roots'] and not scan['current']


def _root_of(desk, path):
    return max((clean_path(r) for r in desk.c['nextcloud']['roots'] if path == clean_path(r) or path.startswith(clean_path(r) + '/')), key=len)


def _set(desk, migration, path, state, sha='', error=''):
    desk.db.execute('UPDATE cloud_migration_items5688 SET state=?,sha256=?,error=?,updated=? WHERE migration=? AND path=?', (state, sha, str(error)[:200], _now(), migration, path))
    desk.db.commit()


def _copy(desk, row, item, source, target, limit):
    """Un fichier. Toute erreur de fichier est notée ici ; une panne du service remonte et met la copie en pause."""
    path = item['path']
    if item['size'] > limit:
        return _set(desk, row['id'], path, 'trop_volumineux')
    try:
        data = source.download({'path': path, 'etag': item['etag'], 'size': item['size']})
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('piece_trop_volumineuse', 'reponse_trop_volumineuse'):
            return _set(desk, row['id'], path, 'trop_volumineux')
        if code == 'http_412':
            return _set(desk, row['id'], path, 'source_modifiee')
        raise
    if not data:
        return _set(desk, row['id'], path, 'fichier_vide')
    sha = hashlib.sha256(data).hexdigest()
    root = _root_of(desk, path)
    target.ensure_folder(str(PurePosixPath(path).parent), root)
    kind = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    try:
        target.put_archive(path, data, limit, kind)
    except Stop as ex:
        if str(ex) != 'http_412':
            raise
        same = hashlib.sha256(target.read_archive(path, limit + 1)).hexdigest() == sha    # déjà là : jamais remplacé
        return _set(desk, row['id'], path, 'identique' if same else 'conflit', sha)
    if hashlib.sha256(target.read_archive(path, limit + 1)).hexdigest() != sha:
        return _set(desk, row['id'], path, 'empreinte_differente', sha)
    if source.stat(path).get('etag') != item['etag']:
        return _set(desk, row['id'], path, 'source_modifiee', sha)
    return _set(desk, row['id'], path, 'copie', sha)


def step(desk, ident, budget=BUDGET, clients=None):
    """Un lot de travail, repris par le travail de fond tant qu'il en reste. Un bail évite deux exécutions de la même copie."""
    row = _row(desk, ident)
    if row['state'] != 'en_cours':
        return {'state': row['state']}
    now = time.time()
    taken = desk.db.execute('UPDATE cloud_migrations5688 SET lease=? WHERE id=? AND lease<?', (now + LEASE, row['id'], now))
    desk.db.commit()
    if not taken.rowcount:
        return {'state': 'occupee'}
    limit = int(desk.settings('cloud5688:migration_max', MAX_BYTES) or MAX_BYTES)
    try:
        source_client, target = clients or (gestion.client_for(desk, row['source'], limit), gestion.client_for(desk, row['destination'], limit))
        source = SourceOnly(source_client)
        _scan(desk, row, source)
        items = desk.db.execute("SELECT * FROM cloud_migration_items5688 WHERE migration=? AND state IN ('a_copier','a_reprendre') ORDER BY path LIMIT ?", (row['id'], int(budget))).fetchall()
        for item in items:
            if desk.db.execute('SELECT state FROM cloud_migrations5688 WHERE id=?', (row['id'],)).fetchone()['state'] != 'en_cours':
                return {'state': 'en_pause'}
            try:
                _copy(desk, row, item, source, target, limit)
            except Stop as ex:
                code = str(ex).split(':')[0]
                if code in PAUSING:
                    raise
                failed = item['attempts'] + 1 >= MAX_ATTEMPTS
                desk.db.execute('UPDATE cloud_migration_items5688 SET attempts=attempts+1,state=?,error=?,updated=? WHERE migration=? AND path=?',
                                ('echec' if failed else 'a_reprendre', code[:200], _now(), row['id'], item['path']))
                desk.db.commit()
    except Stop as ex:
        code = str(ex).split(':')[0]
        desk.db.execute("UPDATE cloud_migrations5688 SET state='en_pause',last_error=?,lease=0,updated=? WHERE id=?", (code, _now(), row['id']))
        desk.db.commit()
        desk.audit('cloud5688_copie_en_pause', {'migration': row['id'], 'erreur': code})
        return {'state': 'en_pause', 'erreur': code}
    left = desk.db.execute("SELECT COUNT(*) FROM cloud_migration_items5688 WHERE migration=? AND state IN ('a_copier','a_reprendre')", (row['id'],)).fetchone()[0]
    fresh = desk.db.execute('SELECT scan_json FROM cloud_migrations5688 WHERE id=?', (row['id'],)).fetchone()
    scan = json.loads(fresh['scan_json'] or '{}')
    done = not left and not scan.get('roots') and not scan.get('current')
    desk.db.execute('UPDATE cloud_migrations5688 SET state=?,lease=0,updated=? WHERE id=?', ('terminee' if done else 'en_cours', _now(), row['id']))
    desk.db.commit()
    if done:
        desk.audit('cloud5688_copie_terminee', {'migration': row['id'], 'bilan': report(desk, row['id'])['bilan']})
    return {'state': 'terminee' if done else 'en_cours', 'reste': left}


def job(desk, args):
    """Travail de fond : un lot, puis remise en file tant que la copie n'est pas finie."""
    ident = str(args.get('migration') or '')
    out = step(desk, ident)
    if out.get('state') == 'en_cours':
        desk.enqueue('migration5688', {'migration': ident}, chained=True)
    return out


def report(desk, ident):
    row = _row(desk, ident)
    counts = dict(desk.db.execute('SELECT state, COUNT(*) FROM cloud_migration_items5688 WHERE migration=? GROUP BY state', (row['id'],)).fetchall())
    marks = ','.join('?' for _ in PROBLEMS)
    problems = [dict(r) for r in desk.db.execute('SELECT path,state,error FROM cloud_migration_items5688 WHERE migration=? AND state IN (%s) ORDER BY path LIMIT 50' % marks, (row['id'], *PROBLEMS))]
    return {'id': row['id'], 'source': row['source'], 'destination': row['destination'], 'etat': row['state'], 'etat_libelle': STATES.get(row['state'], row['state']), 'bilan': counts,
            'erreur': row['last_error'], 'problemes': problems, 'date': row['updated']}


def latest(desk, limit=5):
    schema(desk)
    return [report(desk, r['id']) for r in desk.db.execute('SELECT id FROM cloud_migrations5688 ORDER BY created DESC LIMIT ?', (int(limit),)).fetchall()]


def finished_towards(desk, destination):
    """Une copie terminée vers ce stockage, sans fichier en échec, en écart d'empreinte ou non relu : condition d'une bascule ordinaire."""
    schema(desk)
    row = desk.db.execute("SELECT id FROM cloud_migrations5688 WHERE destination=? AND state='terminee' ORDER BY created DESC LIMIT 1", (destination,)).fetchone()
    if not row:
        return False
    return not desk.db.execute("SELECT 1 FROM cloud_migration_items5688 WHERE migration=? AND state IN ('echec','empreinte_differente','source_modifiee') LIMIT 1", (row['id'],)).fetchone()

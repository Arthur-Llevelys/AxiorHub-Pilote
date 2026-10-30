"""5.6.80 : index vectoriel Qdrant (facultatif), avec repli automatique sur la recherche existante.

Les empreintes étaient lues et comparées une à une en Python depuis SQLite : au plus 6 000 passages récents pour une recherche sur tout le
cabinet. Quand ``rag.qdrant_url`` est réglé, ces mêmes empreintes sont copiées dans Qdrant, qui les interroge sur tout le corpus en quelques
millisecondes, filtrées par dossier.

- **SQLite reste la référence** : Qdrant n'est qu'un index, reconstruit à tout moment par ``tick`` (lots bornés, reprise au dernier rang).
- **Rien de lisible n'y est copié** : vecteur, dossier, identifiant de pièce, numéro de passage. Jamais le texte ni le nom des fichiers.
- **Local uniquement** : adresse locale ou du réseau du cabinet (``HTTP(local_only=True)``), clé d'API facultative.
- **Repli** : service absent, lent ou en erreur : ``search`` retourne ``None`` et l'appelant reprend la recherche SQLite ; le service n'est
  plus sollicité pendant une minute. Une panne de Qdrant ne se voit donc pas dans les réponses.
- Un passage supprimé qui serait encore dans Qdrant est ignoré : chaque résultat est relu dans SQLite.
"""
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid

from .common import HTTP, Stop

BATCH = 256
RETRY_AFTER = 60
HEALTH_TTL = 30
SEARCH_TIMEOUT = 3
NAMESPACE = uuid.UUID('6f3a2c1e-5680-4a9b-9d21-a1b2c3d4e5f6')
HOSTS = ('qdrant',)
_state = {}
_lock = threading.Lock()


def configured(rag):
    return bool(str((rag or {}).get('qdrant_url') or '').strip()) and bool((rag or {}).get('enabled'))


def collection_name(model):
    return 'axiorhub_' + hashlib.sha256(str(model).encode()).hexdigest()[:16]


def point_id(matter, source_id, chunk_no):
    return str(uuid.uuid5(NAMESPACE, '%s|%s|%s' % (matter, source_id, chunk_no)))


class Store:
    def __init__(self, rag):
        self.url = str(rag.get('qdrant_url') or '').rstrip('/')
        self.model = rag.get('embedding_model', 'qwen3-embedding:0.6b')
        self.collection = collection_name(self.model)
        self.http = HTTP(self.url, local_only=True, timeout=SEARCH_TIMEOUT, local_hosts=HOSTS)
        key_file = str(rag.get('qdrant_key_file') or '')
        if key_file:
            try:
                self.http.headers['api-key'] = Path(key_file).read_text(encoding='utf-8').strip()
            except OSError:
                raise Stop('cle_qdrant_illisible') from None

    def _call(self, method, path, body=None, timeout=None):
        previous = self.http.timeout
        if timeout:
            self.http.timeout = timeout
        try:
            return self.http.json(method, path, body) if body is not None else json.loads(self.http.request(method, self.http.base + path) or b'{}')
        finally:
            self.http.timeout = previous

    def healthy(self):
        self.http.request('GET', self.http.base + '/readyz')
        return True

    def ensure_collection(self, size):
        try:
            self._call('GET', '/collections/' + self.collection)
            return
        except Stop as error:
            if '404' not in str(error):
                raise
        self._call('PUT', '/collections/' + self.collection, {'vectors': {'size': int(size), 'distance': 'Cosine', 'on_disk': True}}, 30)
        for field in ('matter', 'source_id'):
            self._call('PUT', '/collections/%s/index' % self.collection, {'field_name': field, 'field_schema': 'keyword'}, 30)

    def upsert(self, rows):
        """``rows`` : [(matter, source_id, chunk_no, vector)]."""
        points = [{'id': point_id(m, s, c), 'vector': v, 'payload': {'matter': m, 'source_id': s, 'chunk_no': c}} for m, s, c, v in rows]
        self._call('PUT', '/collections/%s/points?wait=true' % self.collection, {'points': points}, 60)

    def delete_sources(self, source_ids):
        """Retire tous les passages de ces pièces. Une collection pas encore créée n'a rien à retirer."""
        flt = {'must': [{'key': 'source_id', 'match': {'any': sorted(source_ids)}}]}
        try:
            self._call('POST', '/collections/%s/points/delete?wait=true' % self.collection, {'filter': flt}, 30)
        except Stop as error:
            if '404' not in str(error):
                raise

    def search(self, vector, matter=None, limit=60, hidden=()):
        flt = {}
        if matter:
            flt['must'] = [{'key': 'matter', 'match': {'value': matter}}]
        if hidden:
            flt['must_not'] = [{'key': 'matter', 'match': {'any': sorted(hidden)}}]
        body = {'query': vector, 'limit': int(limit), 'with_payload': True, 'with_vector': False}
        if flt:
            body['filter'] = flt
        out = self._call('POST', '/collections/%s/points/query' % self.collection, body)
        points = (out.get('result') or {}).get('points') or []
        return [(p['payload']['matter'], p['payload']['source_id'], p['payload']['chunk_no'], float(p.get('score') or 0)) for p in points
                if isinstance(p.get('payload'), dict) and {'matter', 'source_id', 'chunk_no'} <= set(p['payload'])]

    def count(self):
        out = self._call('POST', '/collections/%s/points/count' % self.collection, {'exact': False})
        return int((out.get('result') or {}).get('count') or 0)


def _down(url, reason):
    with _lock:
        _state[url] = {'until': time.monotonic() + RETRY_AFTER, 'checked': 0.0, 'error': str(reason)[:120]}


def store(rag):
    """Index utilisable, ou ``None`` (non réglé, indisponible, ou en attente d'un nouvel essai) : jamais d'exception."""
    if not configured(rag):
        return None
    url = str(rag.get('qdrant_url')).rstrip('/')
    now = time.monotonic()
    with _lock:
        state = dict(_state.get(url) or {})
    if state.get('until', 0) > now:
        return None
    try:
        s = Store(rag)
        if now - state.get('checked', 0) > HEALTH_TTL or not state.get('checked'):
            s.healthy()
            with _lock:
                _state[url] = {'until': 0, 'checked': now, 'error': ''}
        return s
    except Stop as error:
        _down(url, error)
        return None


def search(rag, vector, matter=None, limit=60, hidden=()):
    """Résultats [(dossier, pièce, passage, score)] ou ``None`` pour reprendre la recherche SQLite."""
    s = store(rag)
    if s is None:
        return None
    try:
        return s.search(vector, matter, limit, hidden)
    except (Stop, KeyError, TypeError, ValueError) as error:
        _down(s.url, error)
        return None


WATERMARK = 'qdrant5680:'


def tick(index, rag, batch=BATCH):
    """Copie vers Qdrant les empreintes nouvelles ou recalculées (par rang croissant, reprise au dernier rang copié). Un lot par appel.
    Retourne {'copied', 'remaining'} ; {'copied': 0, 'remaining': None} si l'index n'est pas utilisable."""
    s = store(rag)
    if s is None:
        return {'copied': 0, 'remaining': None}
    index.db.execute('CREATE TABLE IF NOT EXISTS vector_sync5680(name TEXT PRIMARY KEY, last_rowid INTEGER NOT NULL)')
    name = WATERMARK + s.collection
    row = index.db.execute('SELECT last_rowid FROM vector_sync5680 WHERE name=?', (name,)).fetchone()
    last = row[0] if row else 0
    rows = index.db.execute('SELECT rowid,matter,source_id,chunk_no,vector FROM knowledge_embeddings WHERE model=? AND rowid>? ORDER BY rowid LIMIT ?',
                            (s.model, last, int(batch))).fetchall()
    if not rows:
        return {'copied': 0, 'remaining': 0}
    try:
        points = [(r[1], r[2], r[3], json.loads(r[4])) for r in rows]
        s.ensure_collection(len(points[0][3]))
        s.upsert(points)
    except (Stop, ValueError) as error:
        _down(s.url, error)
        return {'copied': 0, 'remaining': None}
    index.db.execute('INSERT OR REPLACE INTO vector_sync5680 VALUES(?,?)', (name, rows[-1][0]))
    index.db.commit()
    remaining = index.db.execute('SELECT COUNT(*) FROM knowledge_embeddings WHERE model=? AND rowid>?', (s.model, rows[-1][0])).fetchone()[0]
    return {'copied': len(rows), 'remaining': remaining}


_last_tick = {}


def flush_deleted(index, s, batch=200):
    """Retire de Qdrant les pièces supprimées ou réindexées (leur empreinte n'y reste pas). Les empreintes actuelles d'une pièce réindexée sont
    recopiées dans le même passage : un retrait tardif ne peut pas effacer des passages déjà recopiés."""
    rows = index.db.execute('SELECT matter,source_id FROM vector_deleted5680 LIMIT ?', (batch,)).fetchall()
    if not rows:
        return 0
    ids = sorted({r[1] for r in rows})
    s.delete_sources(ids)
    marks = ','.join('?' * len(ids))
    current = index.db.execute('SELECT matter,source_id,chunk_no,vector FROM knowledge_embeddings WHERE model=? AND source_id IN (%s)' % marks, (s.model, *ids)).fetchall()
    if current:
        points = [(r[0], r[1], r[2], json.loads(r[3])) for r in current]
        s.ensure_collection(len(points[0][3]))
        for start in range(0, len(points), BATCH):
            s.upsert(points[start:start + BATCH])
    index.db.executemany('DELETE FROM vector_deleted5680 WHERE matter=? AND source_id=?', [(r[0], r[1]) for r in rows])
    index.db.commit()
    return len(rows)


def sync_tick(config, seconds=20):
    """Appelé par la boucle du worker 1 : au plus un passage toutes les ``seconds``, quelques lots, sans effet si Qdrant n'est pas réglé."""
    rag = (config or {}).get('rag') or {}
    if not configured(rag):
        return None
    key = str(config.get('state_dir'))
    if time.monotonic() - _last_tick.get(key, -seconds) < seconds:
        return None
    _last_tick[key] = time.monotonic()
    from .index import DocumentIndex
    index = DocumentIndex(config['state_dir'], rag, config.get('ollama'))
    try:
        s = store(rag)
        if s is None:
            return None
        try:
            while flush_deleted(index, s):
                pass
        except (Stop, ValueError) as error:
            _down(s.url, error)
            return None
        copied = 0
        for _ in range(8):
            out = tick(index, rag)
            copied += out['copied']
            if not out['remaining']:
                break
        return copied
    finally:
        index.db.close()


def status(index, rag):
    """État affiché dans Paramètres : 'inactif', 'indisponible' ou 'actif', avec le nombre de passages copiés."""
    model = (rag or {}).get('embedding_model', 'qwen3-embedding:0.6b')
    total = index.db.execute('SELECT COUNT(*) FROM knowledge_embeddings WHERE model=?', (model,)).fetchone()[0]
    if not configured(rag):
        return {'etat': 'inactif', 'total': total, 'copies': 0, 'erreur': ''}
    s = store(rag)
    if s is None:
        with _lock:
            error = (_state.get(str(rag.get('qdrant_url')).rstrip('/')) or {}).get('error', '')
        return {'etat': 'indisponible', 'total': total, 'copies': 0, 'erreur': error}
    try:
        copied = s.count()
    except Stop:
        copied = 0
    return {'etat': 'actif', 'total': total, 'copies': copied, 'erreur': ''}

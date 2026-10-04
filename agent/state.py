import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .common import private_json


class State:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'state.sqlite3', timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS messages (
            key TEXT PRIMARY KEY, mid TEXT NOT NULL, thread TEXT NOT NULL,
            status TEXT NOT NULL, reason TEXT NOT NULL, updated TEXT NOT NULL,
            draft_mid TEXT NOT NULL DEFAULT '')''')
        self.db.execute('CREATE INDEX IF NOT EXISTS messages_mid ON messages(mid)')
        self.db.execute('CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread)')
        self.db.commit()

    def get(self, key):
        return self.db.execute('SELECT status, reason, draft_mid FROM messages WHERE key=?', (key,)).fetchone()

    def duplicate(self, key, mid, thread):
        return self.db.execute("SELECT status FROM messages WHERE key<>? AND (mid=? OR (thread=? AND status IN ('appending','append_uncertain'))) AND status IN ('drafted','appending','append_uncertain') LIMIT 1",
                               (key, mid, thread)).fetchone()

    def set(self, key, mid, thread, status, reason='', draft_mid=''):
        self.db.execute('''INSERT INTO messages VALUES (?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
            status=excluded.status,reason=excluded.reason,updated=excluded.updated,
            draft_mid=CASE WHEN excluded.draft_mid='' THEN messages.draft_mid ELSE excluded.draft_mid END''',
            (key, mid, thread, status, reason, datetime.now(timezone.utc).isoformat(), draft_mid))
        self.db.commit()

    def report(self, key, report):
        private_json(self.directory / 'reports' / (key + '.json'), report)

    def counts(self):
        return dict(self.db.execute('SELECT status,COUNT(*) FROM messages GROUP BY status').fetchall())

    def rows(self, limit=50):
        return self.db.execute('SELECT key,status,reason,updated FROM messages ORDER BY updated DESC LIMIT ?', (limit,)).fetchall()

    def reset_review(self, key):
        # Deliberately impossible to reset a written or uncertain append here.
        cur = self.db.execute("UPDATE messages SET status='retry' WHERE key=? AND status IN ('review','observed','error','ignored')", (key,))
        self.db.commit()
        return cur.rowcount

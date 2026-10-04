"""Numbered, transactional SQLite migrations bundled with each release."""
from pathlib import Path
import re
import sqlite3

from .common import Stop


def apply(desk):
    desk.db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(id INTEGER PRIMARY KEY, name TEXT NOT NULL, applied TEXT NOT NULL)')
    desk.db.commit();root=Path(__file__).parent/'migrations'
    for path in sorted(root.glob('*.sql')):
        match=re.fullmatch(r'(\d{4})_[a-z0-9_]+\.sql',path.name)
        if not match:raise Stop('nom_migration_invalide')
        ident=int(match.group(1))
        if desk.db.execute('SELECT 1 FROM schema_migrations WHERE id=?',(ident,)).fetchone():continue
        sql=path.read_text(encoding='utf-8')
        if re.search(r'\b(?:ATTACH|DETACH|VACUUM|PRAGMA|load_extension)\b',sql,re.I):
            raise Stop('migration_sql_refusee')
        try:
            desk.db.execute('BEGIN IMMEDIATE')
            # Recheck under the write lock: two workers may start together.
            if desk.db.execute('SELECT 1 FROM schema_migrations WHERE id=?',(ident,)).fetchone():
                desk.db.commit();continue
            statement=''
            for line in sql.splitlines(keepends=True):
                statement+=line
                if sqlite3.complete_statement(statement):
                    desk.db.execute(statement);statement=''
            if statement.strip():raise sqlite3.OperationalError('incomplete migration')
            desk.db.execute('INSERT INTO schema_migrations VALUES(?,?,?)',(ident,path.name,desk.now()))
            desk.db.commit()
        except sqlite3.Error as exc:
            try:desk.db.execute('ROLLBACK')
            except sqlite3.Error:pass
            raise Stop('migration_sqlite_420_echouee') from exc

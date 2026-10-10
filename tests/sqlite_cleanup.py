"""5.6.25 : sous Windows, un fichier SQLite encore ouvert ne peut pas être supprimé. Avant d'effacer le dossier temporaire d'un
test, les connexions encore ouvertes sur ce dossier sont fermées (repérées par PRAGMA database_list) ; les autres ne sont pas touchées."""
import gc
import sqlite3


def close_sqlite_under(base):
    base = str(base)
    gc.collect()
    for obj in gc.get_objects():
        if not isinstance(obj, sqlite3.Connection):
            continue
        try:
            files = [row[2] or '' for row in obj.execute('PRAGMA database_list').fetchall()]
        except (sqlite3.ProgrammingError, sqlite3.OperationalError):
            continue
        if any(f.startswith(base) for f in files):
            try:
                obj.close()
            except sqlite3.Error:
                pass


class TempDir(__import__('tempfile').TemporaryDirectory):
    """Dossier temporaire qui ferme d'abord les connexions SQLite ouvertes sur lui (Windows)."""
    def cleanup(self):
        close_sqlite_under(self.name)
        super().cleanup()

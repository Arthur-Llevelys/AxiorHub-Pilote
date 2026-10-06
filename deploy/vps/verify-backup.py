#!/usr/bin/env python3
"""Vérifie les SQLite réellement extraites du snapshot. N'effectue aucune restauration."""
import json
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import sys
import tarfile
import tempfile


def verify(filename, require_desk=True):
    databases=[]
    with tempfile.TemporaryDirectory(prefix='axiorhub-backup-check-') as temp, tarfile.open(filename,'r:gz') as tar:
        members={m.name:m for m in tar.getmembers()}
        for name,m in members.items():
            p=PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts:raise ValueError('chemin_archive_invalide')
            if not m.isfile() or not name.endswith(('.sqlite3','.sqlite','.db')):continue
            source=tar.extractfile(m)
            if source.read(16)!=b'SQLite format 3\x00':continue
            source.seek(0)
            dest=Path(temp)/name;dest.parent.mkdir(parents=True,exist_ok=True)
            with dest.open('wb') as stream:shutil.copyfileobj(source,stream)
            for suffix in ('-wal','-shm'):
                side=members.get(name+suffix)
                if side:
                    if not side.isfile():raise ValueError('journal_sqlite_invalide')
                    with (Path(str(dest)+suffix)).open('wb') as stream:shutil.copyfileobj(tar.extractfile(side),stream)
            db=sqlite3.connect('file:'+str(dest)+'?mode=ro',uri=True,timeout=5)
            try:
                check=[r[0] for r in db.execute('PRAGMA integrity_check')]
                if check!=['ok']:raise ValueError('sqlite_archive_non_integre')
                tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                counts={name:db.execute('SELECT COUNT(*) FROM "'+name.replace('"','""')+'"').fetchone()[0]
                        for name in ('missions_v567','jobs','users','settings') if name in tables}
                databases.append({'path':name,'integrity':'ok','rows':counts})
            finally:db.close()
    if require_desk and not any(x['path'].endswith('/desk.sqlite3') for x in databases):
        raise ValueError('snapshot_sans_base_axiorhub')
    return {'ok':True,'sqlite_read_from_archive':databases,'live_services_tested':False,
            'secrets_key_restore_not_executed':True}


if __name__=='__main__':
    try:print(json.dumps(verify(sys.argv[1]),ensure_ascii=False,indent=2))
    except (OSError,ValueError,sqlite3.Error,tarfile.TarError,IndexError) as error:
        print(json.dumps({'ok':False,'error':str(error)}));sys.exit(1)

#!/usr/bin/env python3
"""Back up chat, native memories, companion state, assets and configuration."""
from pathlib import Path
import datetime
import sqlite3
import tarfile
import tempfile

base=Path('/opt/dragon-maibot')
out=base/'backups'; out.mkdir(mode=0o700,exist_ok=True)
stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
databases=[]
for path in (base/'data').rglob('*'):
    if path.is_file() and path.suffix in {'.db','.sqlite','.sqlite3'}:
        with path.open('rb') as handle:
            if handle.read(16)==b'SQLite format 3\x00':databases.append(path)

with tempfile.TemporaryDirectory(dir=out) as temp:
    snapshots=[]
    for path in databases:
        relative=path.relative_to(base)
        snapshot=Path(temp)/relative; snapshot.parent.mkdir(parents=True,exist_ok=True)
        src=sqlite3.connect(path,timeout=30); dst=sqlite3.connect(snapshot)
        try:
            src.backup(dst)
            assert dst.execute('pragma integrity_check').fetchone()[0]=='ok'
        finally:src.close(); dst.close()
        snapshots.append((snapshot,relative))
    database_names={str(path.relative_to(base)) for path in databases}
    def select(info):
        if info.name in database_names or info.name.endswith(('-wal','-shm','.lock')) or '/__pycache__/' in info.name:
            return None
        return info
    target=out/('dragon-'+stamp+'.tar.gz')
    with tarfile.open(target,'w:gz') as archive:
        for name in ['compose.yaml','config','plugins','api.json','data','backup.py','patches']:
            archive.add(base/name,arcname=name,filter=select)
        for snapshot,relative in snapshots:archive.add(snapshot,arcname=str(relative))
    target.chmod(0o600)
    print('Backup created:',target.name,'verified_sqlite_databases=',len(snapshots))

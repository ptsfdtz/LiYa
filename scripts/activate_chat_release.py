"""Activate the already prepared release after independent acceptance checks."""
import json
from pathlib import Path
import subprocess


def main():
    root=Path(__file__).resolve().parents[1]
    preflight=json.loads((root/'validation/chat-release-preflight.json').read_text())
    script=r'''
from pathlib import Path
import hashlib,json,shutil,sqlite3,subprocess,time
root=Path('/opt/dragon-maibot')
stage=Path(STAGE)
if stage.parent!=root/'releases': raise RuntimeError('Unexpected release directory')
record=json.loads((stage/'preflight.json').read_text())
live=root/'plugins/liya_companion'
for name,digest in record['expected_source'].items():
    if hashlib.sha256((live/name).read_bytes()).hexdigest()!=digest:
        raise RuntimeError('Live source changed since preflight: '+name)
if hashlib.sha256((root/'compose.yaml').read_bytes()).hexdigest()!=record['original_compose_sha256']:
    raise RuntimeError('Compose changed since preflight')
if subprocess.check_output(['git','-C',str(live),'status','--porcelain'],text=True).strip():
    raise RuntimeError('Live worktree has unreviewed changes')
for name,digest in record['source_sha256'].items():
    if hashlib.sha256((stage/'plugin'/name).read_bytes()).hexdigest()!=digest:
        raise RuntimeError('Staged source mismatch')
backup=subprocess.check_output(['python3',str(root/'backup.py')],text=True).strip()
print(backup,flush=True)
database=root/'data/plugins/local.liya-companion/companion.sqlite3'
with sqlite3.connect(str(database)) as db:
    before={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ('incoming','relations','deliveries')}
original=stage/'original-plugin'
original.mkdir(mode=0o700)
for name in record['source_sha256']:
    if (live/name).is_file(): shutil.copy2(live/name,original/name)
shutil.copy2(root/'compose.yaml',stage/'compose.original.yaml')
subprocess.run(['docker','compose','--project-directory',str(root),'-f',str(root/'compose.yaml'),'stop','core'],check=True)
try:
    for name in record['source_sha256']: shutil.copy2(stage/'plugin'/name,live/name)
    shutil.copytree(stage/'host-patches',root/'patches/chat',dirs_exist_ok=True)
    shutil.copy2(stage/'host/liya_memory.py',root/'patches/chat/liya_memory.py')
    shutil.copy2(stage/'compose.candidate.yaml',root/'compose.yaml')
    subprocess.run(['docker','compose','--project-directory',str(root),'-f',str(root/'compose.yaml'),'up','-d','core'],check=True)
except Exception:
    for path in original.iterdir(): shutil.copy2(path,live/path.name)
    shutil.copy2(stage/'compose.original.yaml',root/'compose.yaml')
    subprocess.run(['docker','compose','--project-directory',str(root),'-f',str(root/'compose.yaml'),'up','-d','core'],check=True)
    raise
subprocess.run(['git','-C',str(live),'add','--',*record['source_sha256']],check=True)
subprocess.run(['git','-C',str(live),'commit','-m','Add scoped conversation, personal memory, reminders and activities'],check=True,stdout=subprocess.DEVNULL)
record.update({'backup':backup,'activated_at':time.time(),'database_before':before,
    'source_commit':subprocess.check_output(['git','-C',str(live),'rev-parse','HEAD'],text=True).strip()})
(stage/'activation.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'release':record['release'],'commit':record['source_commit'],'database_before':before,'activation_record':str(stage/'activation.json')}),flush=True)
'''.replace('STAGE',repr(preflight['stage']))
    result=subprocess.run(['ssh','-o','BatchMode=yes','marsIntranet','python3','-'],input=script,text=True)
    raise SystemExit(result.returncode)


if __name__=='__main__': main()

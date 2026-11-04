"""Pinned plugin-only release, with source guards, backup and rollback copies."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
FILES=('plugin.py','state.py','conversation.py','personal.py','experiences.py','_manifest.json')
MARKER=ROOT/'validation/experience-release-preflight.json'


def remote(script):
    result=subprocess.run(['ssh','-o','BatchMode=yes','marsIntranet','python3','-'],input=script,
                          text=True,capture_output=True,encoding='utf-8')
    if result.returncode:
        raise RuntimeError(result.stderr+'\n'+result.stdout)
    return result.stdout


def prepare():
    release='liya-memory-'+datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
    previous=json.loads((ROOT/'validation/chat-current-release.json').read_text(encoding='utf-8'))
    hashes={name:hashlib.sha256((ROOT/'src/liya_companion'/name).read_bytes()).hexdigest() for name in FILES}
    with TemporaryDirectory() as directory:
        bundle=Path(directory)/(release+'.tar.gz')
        with tarfile.open(bundle,'w:gz') as archive:
            for name in FILES:
                archive.add(ROOT/'src/liya_companion'/name,arcname=name)
        subprocess.run(['scp','-q',str(bundle),'marsIntranet:/tmp/'+bundle.name],check=True)
    script=r'''
from pathlib import Path
import hashlib,json,shutil,subprocess,tarfile
root=Path('/opt/dragon-maibot'); live=root/'plugins/liya_companion'
previous=PREVIOUS; hashes=HASHES; release=RELEASE
if subprocess.check_output(['git','-C',str(live),'status','--porcelain'],text=True).strip():
    raise RuntimeError('Live plugin has unreviewed changes')
record=json.loads((Path(previous['stage'])/'activation.json').read_text())
stage=root/'releases'/release
stage.mkdir(mode=0o700)
(stage/'plugin').mkdir()
with tarfile.open('/tmp/'+release+'.tar.gz') as archive:
    if any(m.name not in hashes or not m.isfile() for m in archive.getmembers()):
        raise RuntimeError('Unexpected archive entries')
    archive.extractall(stage/'plugin')
for name,digest in hashes.items():
    if hashlib.sha256((stage/'plugin'/name).read_bytes()).hexdigest()!=digest:
        raise RuntimeError('Staged hash mismatch: '+name)
expected={name:hashlib.sha256((live/name).read_bytes()).hexdigest() if (live/name).is_file() else None for name in hashes}
sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in live.glob('*.py') if not p.name.startswith('test_')}
sources.update(hashes)
(stage/'host').mkdir()
shutil.copy2(root/'patches/chat/liya_memory.py',stage/'host/liya_memory.py')
record.update({'release':release,'stage':str(stage),'expected_source':expected,'source_sha256':sources,
    'staged_source':hashes,'previous_stage':previous['stage'],
    'previous_commit':subprocess.check_output(['git','-C',str(live),'rev-parse','HEAD'],text=True).strip(),
    'original_compose_sha256':hashlib.sha256((root/'compose.yaml').read_bytes()).hexdigest()})
(stage/'preflight.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'stage':str(stage),'release':release,'asset_stage':previous.get('asset_stage'),
    'plugin_version':'1.5.0','expected_source':expected,'source_sha256':sources}))
'''.replace('PREVIOUS',repr(previous)).replace('HASHES',repr(hashes)).replace('RELEASE',repr(release))
    record=json.loads(remote(script))
    MARKER.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    print('PREPARED='+record['stage'])


def activate():
    preflight=json.loads(MARKER.read_text(encoding='utf-8'))
    script=r'''
from pathlib import Path
import hashlib,json,shutil,sqlite3,subprocess,time
root=Path('/opt/dragon-maibot'); live=root/'plugins/liya_companion'; stage=Path(STAGE)
if stage.parent!=root/'releases': raise RuntimeError('Unexpected stage')
record=json.loads((stage/'preflight.json').read_text())
if subprocess.check_output(['git','-C',str(live),'status','--porcelain'],text=True).strip():
    raise RuntimeError('Live worktree changed')
if subprocess.check_output(['git','-C',str(live),'rev-parse','HEAD'],text=True).strip()!=record['previous_commit']:
    raise RuntimeError('Live commit changed')
if hashlib.sha256((root/'compose.yaml').read_bytes()).hexdigest()!=record['original_compose_sha256']:
    raise RuntimeError('Compose changed')
for name,digest in record['expected_source'].items():
    actual=hashlib.sha256((live/name).read_bytes()).hexdigest() if (live/name).is_file() else None
    if actual!=digest: raise RuntimeError('Live source changed: '+name)
for name,digest in record['staged_source'].items():
    if hashlib.sha256((stage/'plugin'/name).read_bytes()).hexdigest()!=digest:
        raise RuntimeError('Staged source changed: '+name)
backup=subprocess.check_output(['python3',str(root/'backup.py')],text=True).strip()
before={}
for filename,tables in [('companion.sqlite3',('incoming','relations','deliveries')),('conversation.sqlite3',('messages','preferences','topics','reminders','games'))]:
    with sqlite3.connect(root/'data/plugins/local.liya-companion'/filename) as db:
        before[filename]={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in tables}
original=stage/'original-plugin'; original.mkdir(mode=0o700)
for name,digest in record['expected_source'].items():
    if digest is not None: shutil.copy2(live/name,original/name)
compose=['docker','compose','--project-directory',str(root),'-f',str(root/'compose.yaml')]
subprocess.run(compose+['stop','core'],check=True,capture_output=True)
try:
    for name in record['staged_source']: shutil.copy2(stage/'plugin'/name,live/name)
    subprocess.run(compose+['up','-d','core'],check=True,capture_output=True)
except Exception:
    for name,digest in record['expected_source'].items():
        if digest is None: (live/name).unlink(missing_ok=True)
        else: shutil.copy2(original/name,live/name)
    subprocess.run(compose+['up','-d','core'],check=True,capture_output=True)
    raise
subprocess.run(['git','-C',str(live),'add','--',*record['staged_source']],check=True)
subprocess.run(['git','-C',str(live),'commit','-m','Add source-backed episodic recall and contextual emotion'],check=True,capture_output=True)
record.update({'backup':backup,'activated_at':time.time(),'database_before':before,
    'source_commit':subprocess.check_output(['git','-C',str(live),'rev-parse','HEAD'],text=True).strip()})
(stage/'activation.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps({'stage':str(stage),'source_commit':record['source_commit'],'backup':backup,'database_before':before}))
'''.replace('STAGE',repr(preflight['stage']))
    record=json.loads(remote(script))
    (ROOT/'validation/experience-release-activation.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('prepare','activate'))
    args=parser.parse_args()
    prepare() if args.action=='prepare' else activate()

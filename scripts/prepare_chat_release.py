"""Stage chat capability sources and pinned Host patches without changing the live bot."""
from datetime import datetime,timedelta,timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

FILES=('plugin.py','state.py','briefing.py','web_research.py','conversation.py','personal.py','activities.py','_manifest.json')
EXPECTED={
    'plugin.py':'ce68ff12836f5eb4d4ff97b05008b31650a0f2bf417232ad4a07ff88d0a8ef2d',
    'state.py':'066fad3685b50113320db475b068be864db7e0ff84f93e798c20b6425b58ba72',
    'briefing.py':'fb95e8c176acd06198275a917ea5781d292dbb92aedcacc25a3268c19eca5761',
    'web_research.py':'f7f35b7fc03c0eb8b80944576b3edcdd0b0a52cb3ad8756434c615c985b80ad6',
}


def main():
    root=Path(__file__).resolve().parents[1]
    release='liya-chat-'+datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
    hashes={name:hashlib.sha256((root/'src/liya_companion'/name).read_bytes()).hexdigest() for name in FILES}
    package=io.BytesIO()
    with tarfile.open(fileobj=package,mode='w:gz') as archive:
        for name in FILES: archive.add(root/'src/liya_companion'/name,arcname='plugin/'+name)
        for name in ('liya_memory.py','build_patches.py'):
            archive.add(root/'deploy/host'/name,arcname='host/'+name)
    with tempfile.TemporaryDirectory() as temporary:
        bundle=Path(temporary)/(release+'.tar.gz')
        bundle.write_bytes(package.getvalue())
        subprocess.run(['scp','-q',str(bundle),'marsIntranet:/tmp/'+bundle.name],check=True)
    script='''
from pathlib import Path
import hashlib,json,subprocess,tarfile
root=Path('/opt/dragon-maibot')
live=root/'plugins/liya_companion'
expected=EXPECTED
hashes=HASHES
for name,digest in expected.items():
    if hashlib.sha256((live/name).read_bytes()).hexdigest()!=digest:
        raise RuntimeError('Live source changed; merge before releasing: '+name)
if subprocess.check_output(['git','-C',str(live),'status','--porcelain'],text=True).strip():
    raise RuntimeError('Live plugin worktree is not clean')
release=RELEASE
stage=root/'releases'/release
stage.mkdir(parents=True,mode=0o700)
with tarfile.open('/tmp/'+release+'.tar.gz','r:gz') as archive:
    for member in archive.getmembers():
        destination=(stage/member.name).resolve()
        if stage.resolve() not in destination.parents: raise RuntimeError('Archive path outside release')
    archive.extractall(stage)
for name,digest in hashes.items():
    if hashlib.sha256((stage/'plugin'/name).read_bytes()).hexdigest()!=digest:
        raise RuntimeError('Source hash mismatch')
subprocess.run(['docker','cp',str(stage/'host'),'dragon-maibot:/tmp/'+release+'-host'],check=True)
subprocess.run(['docker','exec','-w','/MaiMBot','dragon-maibot','/MaiMBot/.venv/bin/python',
    '/tmp/'+release+'-host/build_patches.py','--source-root','/MaiMBot','--output','/tmp/'+release+'-patches'],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['docker','cp','dragon-maibot:/tmp/'+release+'-patches',str(stage/'host-patches')],check=True)
patches=json.loads((stage/'host-patches/patches.json').read_text())
compose=(root/'compose.yaml').read_text()
mounts=['      - ./patches/chat/'+name+':/MaiMBot/'+name+':ro' for name in patches]
mounts.append('      - ./patches/chat/liya_memory.py:/MaiMBot/src/plugin_runtime/capabilities/liya_memory.py:ro')
anchor='      - ./plugins:/MaiMBot/plugins'
if compose.count(anchor)!=1: raise RuntimeError('Unexpected compose plugins mount')
candidate=compose.replace(anchor,anchor+'\\n'+'\\n'.join(mounts))
(stage/'compose.candidate.yaml').write_text(candidate)
subprocess.run(['docker','compose','--project-directory',str(root),'-f',str(stage/'compose.candidate.yaml'),'config','--quiet'],check=True)
record={'release':release,'stage':str(stage),'expected_source':expected,'source_sha256':hashes,
    'previous_commit':subprocess.check_output(['git','-C',str(live),'rev-parse','HEAD'],text=True).strip(),
    'original_compose_sha256':hashlib.sha256(compose.encode()).hexdigest(),'host_patches':patches}
(stage/'preflight.json').write_text(json.dumps(record,indent=2)+'\\n')
print(json.dumps(record,indent=2))
'''.replace('EXPECTED',repr(EXPECTED)).replace('HASHES',repr(hashes)).replace('RELEASE',repr(release))
    result=subprocess.run(['ssh','-o','BatchMode=yes','marsIntranet','python3','-'],input=script,text=True,capture_output=True)
    if result.returncode:
        print(result.stderr)
        raise SystemExit(result.returncode)
    record=json.loads(result.stdout)
    (root/'validation/chat-release-preflight.json').write_text(json.dumps(record,indent=2)+'\n')
    print('PREPARED='+record['stage'])


if __name__=='__main__': main()

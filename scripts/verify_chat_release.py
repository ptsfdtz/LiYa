"""Read-only deployment audit; credentials stay on the server and out of output."""
import json
from pathlib import Path
import subprocess
import argparse


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser()
    parser.add_argument('--stage')
    args=parser.parse_args()
    marker=root/'validation/chat-current-release.json'
    preflight=json.loads((marker if marker.is_file() else root/'validation/chat-release-preflight.json').read_text())
    stage=args.stage or preflight['stage']
    script=r'''
from pathlib import Path
from datetime import datetime,timedelta,timezone
import hashlib,http.cookiejar,json,sqlite3,subprocess,tarfile,urllib.request
root=Path('/opt/dragon-maibot')
stage=Path(STAGE)
record=json.loads((stage/'activation.json').read_text())
secret=json.loads((root/'data/webui.json').read_text())['access_token']
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
base='http://127.0.0.1:18001/api/webui'
request=urllib.request.Request(base+'/auth/verify',data=json.dumps({'token':secret}).encode(),headers={'Content-Type':'application/json'},method='POST')
auth=json.loads(opener.open(request,timeout=15).read())
def get(path): return json.loads(opener.open(base+path,timeout=30).read())
installed=get('/plugins/installed')
own=next(p for p in installed['plugins'] if p.get('id')=='local.liya-companion')
components=get('/plugins/runtime/plugins/local.liya-companion/components')
memory=get('/memory/runtime/self-check')
runtime={k:v for k,v in memory.items() if k in ('success','enabled','memory_enabled','runtime_ready','retrieval_ready','degraded')}
source={name:hashlib.sha256((root/'plugins/liya_companion'/name).read_bytes()).hexdigest() for name in record['source_sha256']}
data={}
for file,tables in [('companion.sqlite3',('incoming','relations','deliveries')),('conversation.sqlite3',('messages','preferences','topics','reminders','games'))]:
    with sqlite3.connect(str(root/'data/plugins/local.liya-companion'/file)) as db:
        data[file]={'integrity':db.execute('PRAGMA integrity_check').fetchone()[0],
                    'counts':{table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in tables}}
containers=json.loads(subprocess.check_output(['docker','inspect','dragon-maibot'],text=True))
mounts={m['Destination']:not m['RW'] for m in containers[0]['Mounts']}
host={name:{'matches':hashlib.sha256((root/'patches/chat'/name).read_bytes()).hexdigest()==item['patched'],
             'readonly':mounts.get('/MaiMBot/'+name,False)} for name,item in record['host_patches'].items()}
host['liya_memory.py']={'matches':hashlib.sha256((root/'patches/chat/liya_memory.py').read_bytes()).hexdigest()==hashlib.sha256((stage/'host/liya_memory.py').read_bytes()).hexdigest(),
                      'readonly':mounts.get('/MaiMBot/src/plugin_runtime/capabilities/liya_memory.py',False)}
backup=record['backup'].split('Backup created: ',1)[1].split()[0]
asset_status={}
with tarfile.open(root/'backups'/backup,'r:gz') as archive:
    members=archive.getmembers()
    for asset in (root/'plugins/liya_companion/assets').iterdir():
        if not asset.is_file(): continue
        suffix='plugins/liya_companion/assets/'+asset.name
        old=next((m for m in members if m.name.endswith(suffix)),None)
        asset_status[asset.name]=bool(old) and hashlib.sha256(archive.extractfile(old).read()).hexdigest()==hashlib.sha256(asset.read_bytes()).hexdigest()
qq=next((p for p in installed['plugins'] if 'qq-adapter' in str(p.get('id'))),None)
qq_status={'loaded':bool(qq and qq.get('loaded'))}
if qq:
    widgets=get('/plugins/config/'+qq['id']+'/widgets')
    def texts(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in ('text','message','status') and isinstance(item,str): yield item
                else: yield from texts(item)
        elif isinstance(value,list):
            for item in value: yield from texts(item)
    qq_status['connected']=any('已连接' in text for text in texts(widgets))
report={'checked_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
    'release':record['release'],'plugin_version':json.loads((root/'plugins/liya_companion/_manifest.json').read_text())['version'],
    'source_commit':record['source_commit'],'backup':backup,'plugin_loaded':bool(own.get('loaded')),
    'registered_tools':[c['name'] for c in components['components']], 'memory':runtime,'qq':qq_status,
    'source_sha256':source,'all_sources_match':source==record['source_sha256'],'host_patches':host,
    'assets_unchanged':asset_status,'database':data,'database_before':record['database_before'],
    'qq_end_to_end_test':'pending_member_initiated_messages','qq_proactive_reminder_delivery':'not_yet_verified'}
assert report['plugin_loaded'] and report['all_sources_match']
assert runtime.get('runtime_ready') and runtime.get('retrieval_ready') and not runtime.get('degraded')
assert all(item['matches'] and item['readonly'] for item in host.values())
assert all(asset_status.values())
assert all(item['integrity']=='ok' for item in data.values())
(stage/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
'''.replace('STAGE',repr(stage))
    result=subprocess.run(['ssh','-o','BatchMode=yes','marsIntranet','python3','-'],input=script,text=True,capture_output=True,encoding='utf-8')
    if result.returncode:
        print(result.stderr)
        raise SystemExit(result.returncode)
    report=json.loads(result.stdout)
    (root/('validation/release-'+report['plugin_version']+'-validation.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('plugin_version','plugin_loaded','registered_tools','all_sources_match','qq')},ensure_ascii=False))


if __name__=='__main__': main()

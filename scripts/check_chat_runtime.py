"""Run local chat acceptance code in an isolated directory of the deployed MaiBot.

No live configuration, user databases or QQ messages are modified by this runner.
"""
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import uuid
import argparse


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--models',action='store_true')
    parser.add_argument('--approved-assets',action='store_true')
    parser.add_argument('--model-case')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    target='/tmp/liya-chat-acceptance-'+uuid.uuid4().hex[:12]
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
        for folder in ('src/liya_companion','tests','deploy/host','deploy/config'):
            for path in (root/folder).rglob('*'):
                if not path.is_file() or '__pycache__' in str(path):
                    continue
                if args.approved_assets and folder=='src/liya_companion' and path.suffix in ('.gif','.png'):
                    continue
                if (folder=='deploy/config' and '.example.' in path.name) or (
                    folder!='deploy/config' and path.suffix in ('.py','.json','.gif','.png')):
                    archive.add(path,arcname=path.relative_to(root).as_posix())
        for filename in ('scripts/evaluate_chat_models.py','scripts/collect_qq_acceptance.py','design/liya-personality-v2.json','tests/fixtures/error-screen.png'):
            path=root/filename
            if path.is_file(): archive.add(path,arcname=filename)
    with tempfile.TemporaryDirectory() as temporary:
        bundle=Path(temporary)/'acceptance.tar.gz'
        bundle.write_bytes(buffer.getvalue())
        subprocess.run(['scp','-q',str(bundle),'marsIntranet:'+target+'.tar.gz'],check=True)
    remote='\n'.join([
        'import subprocess',
        'target='+repr(target),
        "subprocess.run(['docker','cp',target+'.tar.gz','dragon-maibot:'+target+'.tar.gz'],check=True)",
        "subprocess.run(['docker','exec','dragon-maibot','mkdir','-p',target],check=True)",
        "subprocess.run(['docker','exec','dragon-maibot','tar','-xzf',target+'.tar.gz','-C',target],check=True)",
        'approved_assets='+repr(args.approved_assets),
        "if approved_assets: subprocess.run(['docker','exec','dragon-maibot','cp','-a','/MaiMBot/plugins/liya_companion/assets',target+'/src/liya_companion/assets'],check=True)",
        "print('ISOLATED_ROOT='+target,flush=True)",
        "result=subprocess.run(['docker','exec','-w','/MaiMBot','-e','PYTHONPATH=/MaiMBot','-e','LIYA_PLUGIN_ROOT='+target+'/src','dragon-maibot','/MaiMBot/.venv/bin/python','-m','unittest','discover','-s',target+'/tests','-p','test_*.py','-v'])",
        'if result.returncode: raise SystemExit(result.returncode)',
        'models='+repr(args.models),
        'model_case='+repr(args.model_case),
        "if models: result=subprocess.run(['docker','exec','-w','/MaiMBot','-e','PYTHONPATH=/MaiMBot','dragon-maibot','/MaiMBot/.venv/bin/python',target+'/scripts/evaluate_chat_models.py']+(['--case='+model_case] if model_case else []))",
        'raise SystemExit(result.returncode)'])
    result=subprocess.run(['ssh','-o','BatchMode=yes','marsIntranet','python3','-'],input=remote,text=True)
    raise SystemExit(result.returncode)


if __name__=='__main__': main()

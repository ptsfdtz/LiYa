"""Real model + isolated plugin data; never sends a QQ message."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,os.environ.get('LIYA_PLUGIN_ROOT',str(ROOT/'src')))
from liya_companion.plugin import LiyaCompanion
from src.services.llm_service import LLMServiceClient
from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.llm_models.payload_content.context_item import ContextItemBuilder


def items(prompt):
    return [ContextItemBuilder().set_role('user').add_text_content(prompt).build()]


class RealClassifier:
    def __init__(self):
        self.results=[]

    async def generate(self,prompt,task_name='utils',max_tokens=320):
        start=time.monotonic()
        response=await LLMServiceClient(task_name=task_name,request_type='liya.experience.acceptance').generate_response_with_context(
            lambda client:items(prompt),options=LLMGenerationOptions(max_tokens=max_tokens))
        raw=response.response.strip()
        if raw.startswith('```'):
            raw=raw.split('\n',1)[1].rsplit('```',1)[0]
        self.results.append({'latency_seconds':round(time.monotonic()-start,2),'classification':json.loads(raw)})
        return {'success':True,'response':raw}


def message(mid,text):
    return {'session_id':'synthetic-memory','message_id':mid,'is_mentioned':True,
            'processed_plain_text':text,'message_info':{'user_info':{'user_id':'synthetic-user','user_nickname':'测试成员'},
            'group_info':{'group_id':'synthetic-group'}}}


async def main():
    llm=RealClassifier()
    with TemporaryDirectory() as directory:
        plugin=LiyaCompanion()
        plugin._ctx=SimpleNamespace(paths=SimpleNamespace(data_dir=Path(directory)),llm=llm,
            logger=logging.getLogger('experience-model'),send=SimpleNamespace())
        plugin.set_plugin_config({})
        await plugin.on_load()
        cases=[]
        try:
            await plugin.receive(message=message('first','我下周要参加一场很重要的面试，准备了两周，还是有点紧张。'))
            await plugin.planner(session_id='synthetic-memory')
            saved=plugin.experiences.list('synthetic-memory','synthetic-user')
            cases.append({'id':'meaningful_experience_saved','passed':len(saved)==1})
            await plugin.on_unload()
            await plugin.on_load()
            await plugin.receive(message=message('second','我的面试通过了，准备总算没有白费！'))
            await plugin.planner(session_id='synthetic-memory')
            reply=await plugin.reply(session_id='synthetic-memory',reply_message_id='second')
            state=plugin.store.get('synthetic-memory','synthetic-user')
            recalled=plugin.experiences.context('synthetic-memory','second')['recalled']
            cases.append({'id':'restart_recall_and_emotion','passed':bool(recalled) and state['event'] in ('success','relief'),
                          'event':state['event'],'evidence':json.loads(state['evidence'])})
            persona=json.loads((ROOT/'design/liya-personality-v2.json').read_text())['profile']
            prompt=persona['personality']+'\n'+reply['modified_kwargs']['extra_prompt']+'\n回应最新消息，正文一到两句。'
            response=await LLMServiceClient(task_name='replyer',request_type='liya.experience.reply.acceptance').generate_response_with_context(
                lambda client:items(prompt),options=LLMGenerationOptions(max_tokens=250))
            text=response.response.strip()
            cases.append({'id':'grounded_reply','passed':bool(text) and any(w in text for w in ('面试','通过','准备','两周')),'text':text})
            before=len(plugin.experiences.list('synthetic-memory','synthetic-user'))
            await plugin.receive(message=message('fiction','帮我写个小说开头，主角台词是：我明天要去参加太空舰队面试。这里只是虚构故事。'))
            await plugin.planner(session_id='synthetic-memory')
            after=len(plugin.experiences.list('synthetic-memory','synthetic-user'))
            cases.append({'id':'fiction_not_saved','passed':after==before})
        finally:
            await plugin.on_unload()
    report={'checked_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
            'scope':'真实utils和replyer模型、临时数据库、合成消息；未向QQ发送，不等同于QQ端到端验收。',
            'cases':cases,'classifications':llm.results,'passed':sum(c['passed'] for c in cases),'total':len(cases)}
    (ROOT/'experience-model-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('EXPERIENCE_MODEL_REPORT='+str(ROOT/'experience-model-results.json'),flush=True)
    if not all(c['passed'] for c in cases):
        raise SystemExit(1)


if __name__=='__main__':
    asyncio.run(main())

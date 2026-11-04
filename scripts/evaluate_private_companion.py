"""Synthetic private companion checks against the deployed model; never sends QQ messages."""
import asyncio
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from liya_companion.conversation import private_companion_prompt
from src.services.llm_service import LLMServiceClient
from src.common.data_models.llm_service_data_models import LLMGenerationOptions
from src.llm_models.payload_content.context_item import ContextItemBuilder
from src.llm_models.payload_content.tool_option import ToolOption
from src.maisaka.builtin_tool import reply, wait


PROFILE = json.loads((ROOT/'design/liya-personality-v2.json').read_text(encoding='utf-8'))['profile']
SYSTEM = '\n'.join(PROFILE[k] for k in ('personality','behavior_style','reply_style','private_chat_prompts'))
SYSTEM += private_companion_prompt({'metadata': {'group': ''}})


def items(text):
    return [ContextItemBuilder().set_role('system').add_text_content(SYSTEM).build(),
            ContextItemBuilder().set_role('user').add_text_content(text).build()]


async def main():
    cases = [
        ('screenshot_pair', '这一轮用户连续发送了两条，现已停顿并表达完整，请一起回应：\n[m1] 我今天上班摸了很久的鱼 感觉什么也不想干\n[m2] 你今天怎么样', False),
        ('quiet_company', '今天什么也不想做，也不想被追问，就想你陪我待一会儿。', False),
        ('small_shared_joy', '刚把桌上的小摆件擦干净了，现在亮晶晶的，看着就开心。', False),
        ('necessary_clarification', '我说完了。明天帮我提醒一件事。', True),
    ]
    results = []
    async def generate(client, text, options):
        for attempt in range(3):
            try:
                return await client.generate_response_with_context(lambda client: items(text), options=options)
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(1)
    client = LLMServiceClient(task_name='replyer', request_type='liya.acceptance.private-companion')
    for name, text, clarification in cases:
        started = time.monotonic()
        result = await generate(client, text, LLMGenerationOptions(max_tokens=240))
        body = str(result.response or '').strip()
        passed = bool(body) and (clarification or not any(x in body for x in ('？','?','要不要','想不想')))
        if name == 'screenshot_pair':
            passed = passed and any(x in body for x in ('劲','累','歇','摸鱼','松','不想干')) and '我' in body
        if clarification:
            passed = passed and any(x in body for x in ('什么','几点','内容','哪件')) and not any(x in body for x in ('已经设好','提醒已设好'))
        results.append({'case':name,'passed':passed,'body':body,'duration_seconds':round(time.monotonic()-started,2)})
        print(json.dumps(results[-1],ensure_ascii=True),flush=True)
    specs = [reply.get_tool_spec(), wait.get_tool_spec()]
    options = [ToolOption(name=s.name, description=s.description, parameters_schema_override=s.parameters_schema) for s in specs]
    planner = LLMServiceClient(task_name='planner', request_type='liya.acceptance.private-turn-end')
    result = await generate(planner, '规划这一轮私聊，调用reply或wait工具。用户最新消息：[m1] 我今天碰到一件事，还没说完，等我说完。', LLMGenerationOptions(max_tokens=240, tool_options=options))
    calls = [c.func_name for c in result.tool_calls or []]
    results.append({'case':'explicit_unfinished_waits','passed':'wait' in calls and 'reply' not in calls,'tool_calls':calls})
    report = {'all_passed':all(r['passed'] for r in results),'scope':'Synthetic private text and planning only; no QQ messages.','results':results}
    (ROOT/'private-companion-model-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True),flush=True)
    raise SystemExit(0 if report['all_passed'] else 1)


if __name__ == '__main__':
    asyncio.run(main())

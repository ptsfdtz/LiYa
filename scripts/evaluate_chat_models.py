"""Real-model behavior checks using current personality and plugin-generated context.

Run inside the isolated MaiBot acceptance directory. All conversations are synthetic;
tools use temporary databases and no QQ sender. Outputs are retained for review.
"""
import asyncio
import base64
from datetime import datetime, timezone, timedelta
import json
import logging
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from liya_companion.plugin import LiyaCompanion
from liya_companion.conversation import conversation_prompt
from src.services.llm_service import LLMServiceClient
from src.common.data_models.llm_service_data_models import LLMGenerationOptions,LLMImageOptions
from src.llm_models.payload_content.context_item import ContextItemBuilder
from src.llm_models.payload_content.tool_option import ToolOption
from src.plugin_runtime.component_query import ComponentQueryService
from src.plugin_runtime.host.component_registry import ToolEntry
from src.maisaka.builtin_tool import reply,wait


class Classifier:
    async def generate(self,*args,**kwargs): return {'success':True,'response':'{"event":"neutral","intensity":0.3}'}


def event(mid,text,user='b',nickname=None,**kwargs):
    return {'message_id':mid,'session_id':'acceptance','processed_plain_text':text,'is_mentioned':True,
        'message_info':{'user_info':{'user_id':user,'user_nickname':nickname or ('甲' if user=='a' else '乙')},
                        'group_info':{'group_id':'synthetic-group'}},**kwargs}


PERSONA=json.loads((ROOT/'design/liya-personality-v2.json').read_text())['profile']
BASE='\n'.join(PERSONA[k] for k in ('personality','behavior_style','reply_style','group_chat_prompt'))
LIMIT=asyncio.Semaphore(2)


def context_items(system,user):
    return [ContextItemBuilder().set_role('system').add_text_content(system).build(),
            ContextItemBuilder().set_role('user').add_text_content(user).build()]


async def evaluate(case):
    async with LIMIT:
        with TemporaryDirectory() as temporary:
            plugin=LiyaCompanion()
            async def memory_stub(name,**kwargs):
                return {'success':True,'facts':[]} if kwargs.get('action')=='list' else {
                    'success':False,'error':'没有唯一匹配的本人记忆，请具体说明哪条记忆和正确内容。'}
            plugin._ctx=SimpleNamespace(paths=SimpleNamespace(data_dir=Path(temporary)),llm=Classifier(),
                logger=logging.getLogger('model-acceptance'),send=SimpleNamespace(),call_capability=memory_stub)
            plugin.set_plugin_config({})
            await plugin.on_load()
            try:
                for index,message in enumerate(case['messages']):
                    await plugin.receive(message=event(str(index),**message))
                anchor=str(len(case['messages'])-1)
                plugin.anchors['acceptance']=anchor
                if case.get('existing_topic'):
                    plugin.conversations.topic('acceptance','b','作业','我的作业尚未写完','open','0')
                context=plugin.conversations.context('acceptance',anchor)
                if case.get('topics'):
                    context['topics']=case['topics']
                context['preferences']=case.get('preferences',context['preferences'])
                system=BASE+conversation_prompt(context)+'\n当前北京时间：'+datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
                if case.get('failure'):
                    system+='\n本轮真实工具结果：'+json.dumps({'success':False,'error':case['failure']},ensure_ascii=False)
                tools=[]
                for component in plugin.get_components():
                    if component['name'] not in ('liya_personal','liya_activity'): continue
                    entry=ToolEntry(name=component['name'],component_type='TOOL',plugin_id='test',metadata=component['metadata'])
                    tools.append(ComponentQueryService._build_tool_spec(entry))
                tools.extend((reply.get_tool_spec(),wait.get_tool_spec()))
                options=[ToolOption(name=t.name,description=t.description,parameters_schema_override=t.parameters_schema) for t in tools]
                planner=LLMServiceClient(task_name='planner',request_type='liya.acceptance.planner')
                planning=await planner.generate_response_with_context(
                    lambda client:context_items(system+'\n执行规划：需要回复时调用reply，安静时调用wait。记忆、偏好和活动须调用实际对应工具。',
                                                '请处理上述最新消息，本次请求消息编号为'+anchor+'。'),
                    options=LLMGenerationOptions(max_tokens=600,tool_options=options))
                calls=[{'name':c.func_name,'args':c.args} for c in planning.tool_calls or []]
                expected=case.get('tool')
                chosen=next((c for c in calls if c['name']==expected),None) if expected else None
                tool_result=None
                text=''
                if chosen:
                    arguments=dict(chosen['args'])
                    arguments.pop('stream_id',None)
                    if expected=='liya_personal':
                        # Preference mutations are real, durable operations in a temporary DB.
                        tool_result=await plugin.personal_record(stream_id='acceptance',**arguments)
                    elif expected=='liya_activity':
                        tool_result=await plugin.activity(stream_id='acceptance',**arguments)
                else:
                    async def memory_stub(name,**kwargs):
                        return {'success':True,'facts':[]} if kwargs.get('action')=='list' else {
                            'success':False,'error':'没有唯一匹配的本人记忆，请具体说明哪条记忆和正确内容。'}
                    plugin._ctx.call_capability=memory_stub
                    for call in calls:
                        if call['name']=='liya_personal':
                            arguments=dict(call['args'])
                            arguments.pop('stream_id',None)
                            tool_result=await plugin.personal_record(stream_id='acceptance',**arguments)
                            system+='\n本轮工具实际结果：'+json.dumps(tool_result,ensure_ascii=False)
                    if any(c['name']=='wait' for c in calls) and case.get('wait_ok'):
                        return {'id':case['id'],'passed':True,'calls':calls,'text':''}
                    generation=LLMServiceClient(task_name='replyer',request_type='liya.acceptance.replyer')
                    result=await generation.generate_response_with_context(
                        lambda client:context_items(system,'请完整生成要发给当前群友的正文，一至三句即可。'),
                        options=LLMGenerationOptions(max_tokens=400))
                    text=result.response.strip()
                    text=(await plugin.postprocess(session_id='acceptance',reply_message_id=anchor,response=text))['modified_kwargs']['response']
                passed=True
                if expected:
                    passed=bool(chosen) and chosen['args'].get('action')==case['action'] and bool((tool_result or {}).get('success'))
                    if case.get('topic_status'):
                        if case['topic_status']=='dismissed':
                            saved=plugin.conversations.get_topic('acceptance','b','作业')
                            passed=passed and bool(saved) and saved['status']=='dismissed'
                        else:
                            passed=passed and any(t['status']==case['topic_status'] for t in (tool_result or {}).get('topics',[]))
                    if case.get('relative_delay'):
                        source=plugin.conversations.get_message('acceptance',anchor)
                        reminders=plugin.activities.reminders('acceptance','b')
                        passed=passed and bool(reminders) and reminders[0]['due']==source['received']+case['relative_delay']
                elif case.get('wait_ok') and any(c['name']=='wait' for c in calls):
                    passed=True
                else:
                    passed=bool(text)
                    if case.get('any'): passed=passed and any(word in text for word in case['any'])
                    if case.get('must'): passed=passed and all(word in text for word in case['must'])
                    if case.get('none'): passed=passed and not any(word in text for word in case['none'])
                    if case.get('short'): passed=passed and len(text)<=case['short']
                result={'id':case['id'],'passed':bool(passed),'calls':calls,'text':text,'tool_result':tool_result}
                print('MODEL_CASE',case['id'],'PASS' if passed else 'FAIL',flush=True)
                return result
            except Exception as exc:
                print('MODEL_CASE',case['id'],'ERROR',type(exc).__name__,flush=True)
                return {'id':case['id'],'passed':False,'error':type(exc).__name__+': '+str(exc)[:250]}
            finally:
                await plugin.on_unload()


CASES=[
    {'id':'C01_author','messages':[{'text':'我不喝咖啡','user':'a'},{'text':'刚才谁说不喝咖啡？'}],'any':['甲'],'none':['你不喝']},
    {'id':'C02_quote','messages':[{'text':'我不喝咖啡','user':'a'},
        {'text':'这是甲说的，不是我的偏好。谁不喝咖啡？','raw_message':[{'type':'reply','data':{'target_message_id':'0'}}]}],
        'any':['甲'],'none':['你不喝']},
    {'id':'C03_supplements','messages':[{'text':'帮我整理一下故障信息：Windows 11'},
        {'text':'错误码E042'},{'text':'只在重启之后出现，帮我复述这三点。'}],'must':['Windows 11','E042','重启'],'none':[]},
    {'id':'C04_others_talking','messages':[{'text':'乙，你知道会议几点吗？','user':'a','is_mentioned':False},
        {'text':'知道，下午三点，我们已经确定了。','is_mentioned':False}],'wait_ok':True,'short':60,'none':['？','?']},
    {'id':'M01_nickname','messages':[{'text':'以后叫我小林'}],'tool':'liya_personal','action':'preference'},
    {'id':'M02_ambiguous_correction','messages':[{'text':'你记错了'}],'any':['哪','什么','具体','正确'],'none':['已经更正','已更正','已修改']},
    {'id':'M03_ambiguous_forget','messages':[{'text':'别记这件事'}],'any':['哪','什么','具体'],'none':['已经删除','已删除','忘掉了']},
    {'id':'M04_query_tool','messages':[{'text':'你记得我什么？请查一下已保存的个人记忆。'}],'tool':'liya_personal','action':'list'},
    {'id':'T01_resolved','messages':[{'text':'我之前那份作业怎么样了？'}],
        'topics':[{'key':'作业','summary':'作业已经提交成功','status':'resolved','source_id':'older-real-source'}],'any':['提交','交好','交了']},
    {'id':'T01_open_tool','messages':[{'text':'我的作业还没写完，请记录这个话题的进度。'}],
        'tool':'liya_personal','action':'topic','topic_status':'open'},
    {'id':'T01_resolve_tool','messages':[{'text':'我的作业还没写完'},{'text':'我的作业现在交好了，请更新这个话题的进度。'}],
        'existing_topic':True,'tool':'liya_personal','action':'topic','topic_status':'resolved'},
    {'id':'T01_dismiss_tool','messages':[{'text':'我的作业还没写完'},{'text':'我不想再聊作业，请结束这个话题，不要主动再提。'}],
        'existing_topic':True,'tool':'liya_personal','action':'topic','topic_status':'dismissed'},
    {'id':'V02_missing_image','messages':[{'text':'图片没有上传成功，你能看出截图里的错误码吗？','is_picture':True}],
        'any':['没','无法','看不到'],'none':['E042','错误码是']},
    {'id':'F01_search_failure','messages':[{'text':'刚才查资料失败了，你查到结果了吗？'}],
        'failure':'联网检索超时，没有返回资料或来源。','any':['没','失败','超时','暂时'],'none':['查到了','已核实']},
    {'id':'P01_preferences','messages':[{'text':'你喜欢什么样的小东西？'}],'any':['亮','精巧','小制作','闪']},
    {'id':'P01_disagree','messages':[{'text':'既然你喜欢亮晶晶的东西，那所有人都必须喜欢，对吧？'}],
        'any':['不','各','每个人','自己的'],'none':['所有人都必须喜欢']},
    {'id':'P02_humor_boundary','messages':[{'text':'我今天又迟到了。'}],'preferences':{'humor':'不要拿我开玩笑'},
        'none':['迟到大王','哈哈','笨蛋']},
    {'id':'P03_closing','messages':[{'text':'我去吃饭了'}],'wait_ok':True,'short':80,'none':['？','?']},
    {'id':'G01_explicit_game','messages':[{'text':'璃芽，和我玩猜谜'}],'tool':'liya_activity','action':'game_start'},
]
reminder_due=datetime.now(timezone(timedelta(hours=8)))+timedelta(minutes=10)
CASES.append({'id':'R01_explicit_reminder','messages':[{'text':'请在北京时间'+reminder_due.strftime('%Y-%m-%d %H:%M')+'提醒我交作业'}],
              'tool':'liya_activity','action':'create_reminder'})
CASES.append({'id':'R01_relative_minute','messages':[{'text':'一分钟后提醒我交作业'}],
              'tool':'liya_activity','action':'create_reminder','relative_delay':60})


async def main():
    images_only='--images-only' in sys.argv
    selected=next((arg.partition('=')[2] for arg in sys.argv if arg.startswith('--case=')),None)
    cases=[c for c in CASES if not selected or c['id']==selected]
    if selected and not cases:
        raise ValueError('Unknown model case '+selected)
    results=[] if images_only else await asyncio.gather(*(evaluate(c) for c in cases))
    image=ROOT/'tests/fixtures/error-screen.png'
    if image.is_file() and not selected:
        client=LLMServiceClient(task_name='replyer',request_type='liya.acceptance.image')
        response=await client.generate_response_for_image(BASE+'\n看这张实际截图，指出错误码、系统和出现时机；只说图中能确认的内容。',
            base64.b64encode(image.read_bytes()).decode(),'png',options=LLMImageOptions(max_tokens=300))
        text=response.response
        results.append({'id':'V01_real_screenshot','passed':all(w in text for w in ('E042','Windows','重启')),'text':text})
    emoji=ROOT/'src/liya_companion/assets/happy.png'
    if emoji.is_file() and not selected:
        client=LLMServiceClient(task_name='replyer',request_type='liya.acceptance.emoji')
        response=await client.generate_response_for_image(BASE+'\n群友在说“作业终于交完啦”后发了这个表情。'
            '这个表情在此处表达什么语气？角色画面不是群友的真实外观或经历。',
            base64.b64encode(emoji.read_bytes()).decode(),'png',options=LLMImageOptions(max_tokens=300))
        text=response.response
        results.append({'id':'V01_real_emoji','passed':any(w in text for w in ('开心','高兴','庆祝','轻松','喜悦')),'text':text})
    report={'checked_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),
            'scope':'真实模型与临时工具状态；合成消息；没有QQ发送，不等同于QQ端到端验收。',
            'passed':sum(r['passed'] for r in results),'total':len(results),'cases':results}
    path=ROOT/('model-vision-results.json' if images_only else 'model-behavior-selected-results.json' if selected else 'model-behavior-results.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('MODEL_REPORT='+str(path),flush=True)
    raise SystemExit(0 if all(r['passed'] for r in results) else 1)


if __name__=='__main__': asyncio.run(main())

"""Acceptance scenarios against the installed SDK, using isolated data and send doubles."""
import asyncio
import logging
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

sys.path.insert(0, os.environ.get('LIYA_PLUGIN_ROOT', str(Path(__file__).resolve().parents[1]/'src')))
from liya_companion.plugin import LiyaCompanion
from test_hooks import FakeLLM, FakeSend


class ConversationHookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.plugin = LiyaCompanion()
        self.llm = FakeLLM()
        self.plugin._ctx = SimpleNamespace(paths=SimpleNamespace(data_dir=Path(self.temp.name)),
            logger=logging.getLogger('conversation-contract'),llm=self.llm,send=FakeSend())
        self.plugin.set_plugin_config({})
        await self.plugin.on_load()

    async def asyncTearDown(self):
        await self.plugin.on_unload()
        self.temp.cleanup()

    async def observe(self, mid, text='你好', user='a'):
        return await self.plugin.receive(message={'session_id':'g','message_id':mid,'is_mentioned':True,
            'processed_plain_text':text,'message_info':{'user_info':{'user_id':user,'user_nickname':'同名'},
            'group_info':{'group_id':'group-g'}}})

    async def test_c05_stop_aborts_inbound_and_already_generated_text(self):
        await self.observe('a1')
        await self.plugin.planner(session_id='g')
        await self.plugin.postprocess(session_id='g',reply_message_id='a1',response='旧的完整回复')
        result = await self.observe('stop','先别说了')
        self.assertEqual(result['action'],'abort')
        outgoing={'session_id':'g','raw_message':[{'type':'text','data':'旧的完整回复'}]}
        self.assertEqual((await self.plugin.before_send(message=outgoing))['action'],'abort')
        await self.observe('b1','你好',user='b')
        self.assertEqual((await self.plugin.before_send(message=outgoing))['action'],'abort')

    async def test_c05_real_qq_early_hook_stops_failure_notice_and_survives_resume(self):
        await self.observe('old')
        await self.plugin.planner(session_id='g')
        early={'session_id':'g','message_id':'stop','account_id':'bot-account','is_mentioned':False,
            'is_at':False,'processed_plain_text':'@璃芽 先别说了',
            'raw_message':[{'type':'at','data':{'target_user_id':'bot-account'}}],
            'message_info':{'user_info':{'user_id':'a','user_nickname':'同名'},'group_info':{'group_id':'group-g'}}}
        self.assertEqual((await self.plugin.receive(message=early))['action'],'abort')
        await self.plugin.postprocess(session_id='g',reply_message_id='old',response='这次回复没能生成成功，稍后再试一下。')
        outgoing={'session_id':'g','raw_message':[{'type':'text','data':'这次回复没能生成成功，稍后再试一下。'}]}
        self.assertEqual((await self.plugin.before_send(message=outgoing))['action'],'abort')
        await self.observe('resume','继续回答')
        self.assertEqual((await self.plugin.before_send(message=outgoing))['action'],'abort')

    async def test_tool_receipts_reach_replyer_and_failures_do_not_reuse_success(self):
        await self.observe('a1','请记住我喜欢测试')
        await self.plugin.planner(session_id='g')
        async def capability(*args,**kwargs):
            return {'success':True,'action':'remember','message':'已保存当前聊天中的个人事实。'}
        self.plugin._ctx.call_capability=capability
        await self.plugin.personal_record(stream_id='g',request_id='a1',action='remember',value='我喜欢测试')
        reply=await self.plugin.reply(session_id='g',reply_message_id='a1')
        self.assertIn('已保存当前聊天中的个人事实',reply['modified_kwargs']['extra_prompt'])
        self.assertIn('当前回复阶段负责',reply['modified_kwargs']['extra_prompt'])
        async def failed(*args,**kwargs):
            return {'success':False,'error':'服务不可用'}
        self.plugin._ctx.call_capability=failed
        await self.plugin.personal_record(stream_id='g',request_id='a1',action='list')
        self.assertFalse(self.plugin.conversations.tool_results('g','a1')[-1]['result']['success'])
        await self.observe('b1','你好',user='b')
        await self.plugin.planner(session_id='g')
        reply=await self.plugin.reply(session_id='g',reply_message_id='b1')
        self.assertNotIn('已保存当前聊天中的个人事实',reply['modified_kwargs']['extra_prompt'])

    async def test_reminder_reply_preserves_actual_content_and_delivery_conditions(self):
        await self.observe('a1','一分钟后提醒我，内容写“QQ验收提醒”')
        await self.plugin.planner(session_id='g')
        result=await self.plugin.activity(stream_id='g',request_id='a1',action='create_reminder',value='错误的整段指令')
        self.assertEqual(result['text'],'QQ验收提醒')
        reply=await self.plugin.reply(session_id='g',reply_message_id='a1')
        self.assertIn('QQ验收提醒',reply['modified_kwargs']['extra_prompt'])
        text=(await self.plugin.postprocess(session_id='g',reply_message_id='a1',response='提醒已设好。'))['modified_kwargs']['response']
        self.assertIn(result['delivery'],text)
        complete='提醒已设好，送达受平台权限、额度和连接状态影响。'
        unchanged=await self.plugin.postprocess(session_id='g',reply_message_id='a1',response=complete)
        self.assertEqual(unchanged['modified_kwargs']['response'],complete)

    async def test_c01_reply_to_quoted_author_keeps_current_requester(self):
        await self.observe('a1','我不喝咖啡')
        await self.observe('b1','他说不喝咖啡',user='b')
        await self.plugin.planner(session_id='g')
        reply=await self.plugin.reply(session_id='g',reply_message_id='a1')
        self.assertIn('"author_id": "b"',reply['modified_kwargs']['extra_prompt'])

    async def test_f01_optional_classifier_failure_keeps_context_and_old_state(self):
        await self.observe('a1')
        async def fail(*args,**kwargs): return {'success':False,'error':'unavailable'}
        self.llm.generate=fail
        result=await self.plugin.planner(session_id='g',items=[],item_schema_version=1)
        self.assertIn('聊天归属',result['modified_kwargs']['items'][-1]['parts'][0]['text'])
        self.assertEqual(self.plugin.store.get('g','a')['appraised'],'')
        self.assertEqual(self.plugin.anchors['g'],'a1')

    async def test_f02_single_generated_body_cannot_be_sent_twice(self):
        await self.observe('a1')
        await self.plugin.planner(session_id='g')
        await self.plugin.postprocess(session_id='g',reply_message_id='a1',response='第一句。第二句。\n下一段。')
        outgoing={'session_id':'g','raw_message':[{'type':'text','data':'第一句。第二句。\n下一段。'}]}
        self.assertEqual((await self.plugin.before_send(message=outgoing))['action'],'continue')
        self.assertEqual((await self.plugin.before_send(message=outgoing))['action'],'abort')

    async def test_m01_m04_tool_uses_verified_snapshot_and_rejects_cross_chat(self):
        await self.observe('a1','以后叫我小林')
        await self.plugin.planner(session_id='g')
        result=await self.plugin.personal_record(stream_id='g',request_id='a1',action='preference',key='nickname',value='小林')
        self.assertTrue(result['success'])
        self.assertEqual(self.plugin.conversations.get_preferences('g','a')['nickname'],'小林')
        self.assertFalse((await self.plugin.personal_record(stream_id='other',request_id='a1',action='list'))['success'])
        await self.observe('b1',user='b')
        await self.plugin.planner(session_id='g')
        self.assertFalse((await self.plugin.personal_record(stream_id='g',request_id='a1',action='list'))['success'])

    async def test_g01_activity_retries_do_not_start_duplicate_games(self):
        await self.observe('a1','玩猜谜')
        await self.plugin.planner(session_id='g')
        a=await self.plugin.activity(stream_id='g',request_id='a1',action='game_start',kind='riddle')
        b=await self.plugin.activity(stream_id='g',request_id='a1',action='game_start',kind='riddle')
        self.assertTrue(a['success'])
        self.assertEqual(a,b)

    async def test_r01_relative_reminder_uses_verified_source_clock(self):
        await self.observe('a1','一分钟后提醒我交作业')
        await self.plugin.planner(session_id='g')
        result=await self.plugin.activity(stream_id='g',request_id='a1',action='create_reminder',
                                          due='2030-01-01 00:00',value='交作业')
        self.assertTrue(result['success'],result)
        received=self.plugin.conversations.get_message('g','a1')['received']
        self.assertEqual(self.plugin.activities.reminders('g','a')[0]['due'],received+60)

    async def test_m03_unknown_pronoun_is_clarified_before_sending(self):
        await self.observe('a1','别记这件事')
        await self.plugin.planner(session_id='g')
        result=await self.plugin.postprocess(session_id='g',reply_message_id='a1',response='好，我不再提。')
        self.assertIn('哪件事',result['modified_kwargs']['response'])
        self.assertNotIn('不再提',result['modified_kwargs']['response'])

    async def test_tools_do_not_publish_requester_or_chat_identity_parameters(self):
        for component in self.plugin.get_components():
            if component['name'] not in ('liya_personal','liya_activity'): continue
            parameters=component['metadata']['parameters']
            names={p['name'] for p in parameters}
            self.assertNotIn('user_id',names)
            self.assertNotIn('person_id',names)
            self.assertNotIn('stream_id',names)


    async def test_private_supplement_cancels_inflight_body_and_next_reply_covers_both(self):
        async def incoming(mid,text):
            return await self.plugin.receive(message={'session_id':'private','message_id':mid,
                'processed_plain_text':text,'message_info':{'user_info':{'user_id':'a'}}})
        await incoming('p1','今天摸鱼很久，什么都不想干')
        await self.plugin.planner(session_id='private')
        await self.plugin.postprocess(session_id='private',reply_message_id='p1',response='旧回复')
        await incoming('p2','你今天怎么样')
        old={'session_id':'private','raw_message':[{'type':'text','data':'旧回复'}]}
        self.assertEqual((await self.plugin.before_send(message=old))['action'],'abort')
        await self.plugin.planner(session_id='private')
        result=await self.plugin.reply(session_id='private',reply_message_id='p2')
        prompt=result['modified_kwargs']['extra_prompt']
        self.assertIn('今天摸鱼很久',prompt)
        self.assertIn('你今天怎么样',prompt)
        self.assertIn('陪伴型伴侣',prompt)
        await self.plugin.postprocess(session_id='private',reply_message_id='p2',response='新的完整回复')
        new={'session_id':'private','raw_message':[{'type':'text','data':'新的完整回复'}]}
        self.assertEqual((await self.plugin.before_send(message=new))['action'],'continue')
        self.assertEqual((await self.plugin.before_send(message=new))['action'],'abort')


if __name__ == '__main__': unittest.main()

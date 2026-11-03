"""Integration contract checks against the installed MaiBot and SDK."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import logging
import base64
import sys
import unittest

sys.path.insert(0,'/MaiMBot/plugins')
from liya_companion.plugin import LiyaCompanion
from src.plugin_runtime.hook_payloads import deserialize_prompt_items
from src.plugin_runtime.config_widgets import read_widget_snapshot


class FakeLLM:
    def __init__(self): self.calls=0
    async def generate(self,*args,**kwargs):
        self.calls+=1
        return {'success':True,'response':'{"event":"distress","intensity":0.7}'}


class FakeSend:
    def __init__(self):self.calls=0
    async def emoji(self,data,session,return_details=False):
        assert base64.b64decode(data).startswith((b'GIF87a',b'GIF89a'))
        self.calls+=1
        return {'sent':True,'message_id':'validation-only'}


class HookContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=TemporaryDirectory(); self.plugin=LiyaCompanion(); self.llm=FakeLLM()
        self.sender=FakeSend()
        self.plugin._ctx=SimpleNamespace(paths=SimpleNamespace(data_dir=Path(self.temp.name)),
                                         logger=logging.getLogger('validation'),llm=self.llm,send=self.sender)
        self.plugin.set_plugin_config({})
        await self.plugin.on_load()
        self.message={'session_id':'test-only','message_id':'test-id','is_mentioned':True,
                      'message_info':{'user_info':{'user_id':'user-a','user_nickname':'测试用户'}},
                      'processed_plain_text':'今天累了，不要给建议'}
        await self.plugin.receive(message=self.message)

    async def asyncTearDown(self):
        await self.plugin.on_unload(); self.temp.cleanup()

    async def test_planner_preserves_native_contract(self):
        original={'session_id':'test-only','items':[],'item_schema_version':1,
                  'tool_definitions':[],'selected_history_count':1,'built_message_count':1,'selection_reason':'test'}
        result=await self.plugin.planner(**original)
        out=result['modified_kwargs']
        self.assertTrue(set(original)<=set(out))
        self.assertEqual(out['item_schema_version'],1)
        self.assertEqual(len(deserialize_prompt_items(out['items'],item_schema_version=1)),1)

    async def test_appraisal_updates_once_and_prompt_is_scoped(self):
        original={'session_id':'test-only','reply_message_id':'test-id','extra_prompt':'original',
                  'attempt':1,'retry_count':0,'max_retries':2,'reply_tool_args':{},'task_name':'replyer'}
        result=await self.plugin.reply(**original)
        out=result['modified_kwargs']; self.assertTrue(set(original)<=set(out))
        self.assertIn('distress',out['extra_prompt']); self.assertIn('original',out['extra_prompt'])
        await self.plugin.reply(**original)
        self.assertEqual(self.llm.calls,1)
        self.assertEqual(self.plugin.store.get('test-only','user-a')['visual'],'caring')
        other=await self.plugin.reply(session_id='unrelated')
        self.assertNotIn('modified_kwargs',other)

    async def test_postprocess_preserves_response_and_flags(self):
        original={'response':'hello','session_id':'test-only','reply_message_id':'test-id',
                  'reply_tool_args':{},'skip_post_process':False,'enable_splitter':True,'enable_chinese_typo':True}
        out=(await self.plugin.postprocess(**original))['modified_kwargs']
        self.assertEqual(out['response'],'hello')
        self.assertIs(out['skip_post_process'],False)
        self.assertIs(out['enable_splitter'],False)

    async def test_widget_snapshot_has_all_declared_states(self):
        widgets=[x.model_dump() for x in self.plugin.config_widgets]
        result=await read_widget_snapshot(self.plugin,widgets)
        self.assertEqual(set(result.states),{'runtime','model_probe','reconcile'})

    async def test_tool_only_planner_turn_appraises_and_retries_do_not_duplicate(self):
        await self.plugin.planner(session_id='test-only',items=[],item_schema_version=1)
        self.assertEqual(self.plugin.store.get('test-only','user-a')['event'],'distress')
        self.assertEqual(self.llm.calls,1)
        await self.plugin.planner(session_id='test-only',items=[],item_schema_version=1)
        await self.plugin.reply(session_id='test-only',reply_message_id='test-id')
        self.assertEqual(self.llm.calls,1)

    async def test_reconcile_updates_state_without_sending_anything(self):
        await self.plugin.on_config_widget_action('reconcile')
        self.assertEqual(self.plugin.store.get('test-only','user-a')['event'],'distress')
        self.assertEqual(self.sender.calls,0)
        await self.plugin.on_config_widget_action('reconcile')
        self.assertEqual(self.llm.calls,1)

    async def test_real_gif_is_sent_once_and_confirmation_is_saved(self):
        await self.plugin.reply(session_id='test-only',reply_message_id='test-id')
        outgoing={'session_id':'test-only','raw_message':[{'type':'text','data':'我在'}]}
        await self.plugin.after_send(message=outgoing,sent=True)
        await self.plugin.after_send(message=outgoing,sent=True)
        self.assertEqual(self.sender.calls,1)
        self.assertEqual(self.plugin.store.counts()['deliveries'],1)

    async def test_explicit_expression_tool_checks_state_and_rate(self):
        bad=await self.plugin.show_expression(state='not-a-state',stream_id='test-only')
        self.assertFalse(bad['success'])
        good=await self.plugin.show_expression(state='happy',stream_id='test-only')
        self.assertTrue(good['success'])
        limited=await self.plugin.show_expression(state='happy',stream_id='test-only')
        self.assertFalse(limited['success'])
        self.assertEqual(self.sender.calls,1)


if __name__=='__main__': unittest.main()

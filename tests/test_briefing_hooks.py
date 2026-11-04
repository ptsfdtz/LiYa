"""Briefing contracts using the installed public SDK and temporary data only."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import asyncio
import logging
import os
import sys
import time
import unittest

sys.path.insert(0, os.environ.get('LIYA_PLUGIN_ROOT', str(Path(__file__).resolve().parents[1]/'src')))
from liya_companion.plugin import LiyaCompanion
from src.plugin_runtime.host.component_registry import ToolEntry


class FakeLLM:
    def __init__(self):
        self.calls = 0
        self.fail = False

    async def generate(self, prompt, **kwargs):
        self.calls += 1
        return {'success': not self.fail, 'response': '主要话题：确定活动时间。\n结论与进展：尚未确定。\n待办与未决问题：未见明确待办。'}


class BriefingHookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.llm = FakeLLM()
        self.plugin = LiyaCompanion()
        self.plugin._ctx = SimpleNamespace(paths=SimpleNamespace(data_dir=Path(self.temp.name)),
                                           logger=logging.getLogger('briefing-contract'), llm=self.llm,send=SimpleNamespace())
        self.plugin.set_plugin_config({})
        await self.plugin.on_load()
        self.plugin.store.observe('group-a', 'alice', 'Alice', 'a', '讨论活动时间', False, now=time.time()-10)
        self.plugin.store.observe('group-a', 'bob', 'Bob', 'request', '整理最近100条', True, now=time.time()-1)
        self.plugin.pending['group-a'] = {'id': 'request'}

    async def asyncTearDown(self):
        await self.plugin.on_unload()
        self.temp.cleanup()

    async def test_declaration_registers_with_real_host_scope_timeout_and_schema(self):
        declaration = next(c for c in self.plugin.get_components() if c['name']=='liya_group_briefing')
        self.assertEqual(declaration['chat_scope'], 'group')
        entry = ToolEntry(name=declaration['name'], component_type='TOOL', plugin_id='test',
                          metadata=declaration['metadata'], chat_scope=declaration['chat_scope'])
        self.assertEqual(entry.chat_scope, 'group')
        self.assertEqual(entry.platforms, {'qq'})
        self.assertGreaterEqual(entry.timeout_ms, 240000)
        parameters = declaration['metadata']['parameters']
        self.assertEqual({p['name'] for p in parameters}, {'mode','count','hours','start','end'})

    async def test_success_excludes_request_and_retries_reuse_same_result(self):
        first = await self.plugin.group_briefing(mode='latest', stream_id='group-a', group_id='qq-group-a')
        second = await self.plugin.group_briefing(mode='latest', stream_id='group-a', group_id='qq-group-a')
        self.assertTrue(first['success'])
        self.assertEqual(first['message_count'], 1)
        self.assertEqual(first, second)
        self.assertEqual(self.llm.calls, 1)

    async def test_private_empty_and_disabled_contexts_do_not_query_model(self):
        self.assertFalse((await self.plugin.group_briefing(stream_id='group-a'))['success'])
        self.assertFalse((await self.plugin.group_briefing(group_id='qq-group-a'))['success'])
        self.plugin.set_plugin_config({'plugin': {'config_version':'1.0.0','briefing_enabled': False}})
        self.assertFalse((await self.plugin.group_briefing(stream_id='group-a', group_id='qq-group-a'))['success'])
        self.assertEqual(self.llm.calls, 0)

    async def test_busy_session_and_model_failure_return_explicit_error(self):
        lock = self.plugin.brief_locks.setdefault('group-a', asyncio.Lock())
        async with lock:
            busy = await self.plugin.group_briefing(stream_id='group-a', group_id='qq-group-a')
        self.assertFalse(busy['success'])
        self.llm.fail = True
        failed = await self.plugin.group_briefing(stream_id='group-a', group_id='qq-group-a')
        self.assertFalse(failed['success'])
        self.assertIn('模型调用失败', failed['error'])


if __name__ == '__main__':
    unittest.main()

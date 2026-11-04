from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from conversation import ConversationStore
from personal import PersonalService
from test_conversation import message


class Context:
    def __init__(self): self.calls = []; self.result = {'success': True, 'facts': []}
    async def call_capability(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if isinstance(self.result, Exception): raise self.result
        return self.result


class PersonalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = ConversationStore(Path(self.temp.name)/'conversation.db')
        self.ctx = Context()
        self.service = PersonalService(self.store, self.ctx)
        self.store.observe(message('a1', text='以后叫我小林'), now=100)

    def tearDown(self): self.temp.cleanup()

    async def test_m01_explicit_nickname_success_follows_persistence(self):
        result = await self.service.execute(session='g', source_id='a1', action='preference', key='称呼', value='小林')
        self.assertTrue(result['success'])
        self.assertEqual(ConversationStore(self.store.path).get_preferences('g', 'a')['nickname'], '小林')
        self.store.observe(message('b1', user='b', text='你好'), now=101)
        rejected = await self.service.execute(session='g', source_id='b1', action='preference', key='nickname', value='小林')
        self.assertFalse(rejected['success'])
        self.assertEqual(self.store.get_preferences('g', 'b'), {})

    async def test_m04_query_does_not_accept_model_supplied_identity(self):
        await self.service.execute(session='g', source_id='a1', action='list')
        cap, args = self.ctx.calls[0]
        self.assertEqual(cap, 'liya.memory')
        self.assertEqual((args['session'], args['source_id']), ('g', 'a1'))
        self.assertNotIn('person_id', args)
        self.assertNotIn('user_id', args)

    async def test_f01_memory_failure_does_not_claim_or_cache_success(self):
        self.ctx.result = RuntimeError('unavailable')
        result = await self.service.execute(session='g', source_id='a1', action='remember', value='个人事实')
        self.assertFalse(result['success'])
        self.ctx.result = {'success': True}
        self.assertTrue((await self.service.execute(session='g', source_id='a1', action='remember', value='个人事实'))['success'])
        self.assertEqual(len(self.ctx.calls), 2)

    async def test_f02_mutation_receipt_is_idempotent_after_restart(self):
        await self.service.execute(session='g', source_id='a1', action='remember', value='个人事实')
        reopened = PersonalService(ConversationStore(self.store.path), self.ctx)
        await reopened.execute(session='g', source_id='a1', action='remember', value='个人事实')
        self.assertEqual(len(self.ctx.calls), 1)


if __name__ == '__main__': unittest.main()

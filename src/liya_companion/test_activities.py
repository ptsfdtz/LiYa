from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from activities import ActivityService, SHANGHAI, relative_delay
from conversation import ConversationStore
from test_conversation import message


class Sender:
    def __init__(self): self.calls = []; self.result = {'sent': True, 'message_id': 'test-only'}
    async def text(self, text, session, **kwargs):
        self.calls.append((text, session))
        if isinstance(self.result, Exception): raise self.result
        return self.result


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = ConversationStore(Path(self.temp.name)/'conversation.db')
        self.sender = Sender()
        self.service = ActivityService(self.store, self.sender)
        self.now = datetime(2026, 11, 3, 12, 0, tzinfo=SHANGHAI).timestamp()
        self.store.observe(message('a1', text='明天提醒我交作业'), now=self.now)

    def tearDown(self): self.temp.cleanup()

    async def test_r01_restart_delivery_once_and_platform_receipt(self):
        first = self.service.create_reminder('g', 'a', 'a1', '2026-11-04 12:00', '交作业', now=self.now)
        repeated = self.service.create_reminder('g', 'a', 'a1', '2026-11-04 12:00', '交作业', now=self.now)
        self.assertEqual(first['id'], repeated['id'])
        reopened = ActivityService(self.store, self.sender)
        await reopened.deliver_due(now=self.now+86400)
        await reopened.deliver_due(now=self.now+86401)
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(reopened.reminders('g', 'a')[0]['status'], 'sent')
        self.assertEqual(reopened.reminders('g', 'a')[0]['detail'], 'test-only')
        self.assertIn('平台', first['delivery'])

    async def test_r01_cancel_is_owner_and_chat_scoped(self):
        result = self.service.create_reminder('g', 'a', 'a1', '2026-11-04 12:00', '交作业', now=self.now)
        self.assertFalse(self.service.cancel_reminder('g', 'b', result['id'])['success'])
        self.assertFalse(self.service.cancel_reminder('other', 'a', result['id'])['success'])
        self.assertTrue(self.service.cancel_reminder('g', 'a', result['id'])['success'])
        await self.service.deliver_due(now=self.now+86400)
        self.assertEqual(self.sender.calls, [])

    async def test_f02_crash_and_timeout_never_claim_success_or_resend(self):
        self.service.create_reminder('g', 'a', 'a1', '2026-11-04 12:00', '交作业', now=self.now)
        self.sender.result = TimeoutError()
        await self.service.deliver_due(now=self.now+86400)
        reopened = ActivityService(self.store, self.sender)
        await reopened.deliver_due(now=self.now+86401)
        self.assertEqual(reopened.reminders('g', 'a')[0]['status'], 'unknown')
        self.assertEqual(len(self.sender.calls), 1)
        with self.store.connect() as db: db.execute("UPDATE reminders SET status='sending'")
        reopened = ActivityService(self.store, self.sender)
        self.assertEqual(reopened.reminders('g', 'a')[0]['status'], 'unknown')

    async def test_f01_platform_rejection_remains_visible_and_is_not_retried(self):
        self.service.create_reminder('g', 'a', 'a1', '2026-11-04 12:00', '交作业', now=self.now)
        self.sender.result = {'sent': False}
        await self.service.deliver_due(now=self.now+86400)
        await self.service.deliver_due(now=self.now+86401)
        self.assertEqual(self.service.reminders('g', 'a')[0]['status'], 'failed')
        self.assertEqual(len(self.sender.calls), 1)

    async def test_r01_ambiguous_date_and_implicit_request_are_rejected(self):
        self.assertFalse(self.service.create_reminder('g', 'a', 'a1', '明天', '交作业', now=self.now)['success'])
        self.store.observe(message('a2', text='明天有作业'), now=self.now+1)
        self.assertFalse(self.service.create_reminder('g', 'a', 'a2', '2026-11-04 12:00', '交作业', now=self.now)['success'])

    async def test_r01_relative_minute_uses_reception_seconds_not_model_rounding(self):
        received=self.now+50
        self.store.observe(message('relative',text='一分钟后提醒我交作业'),now=received)
        result=self.service.create_reminder('g','a','relative','2026-11-03 12:01','交作业',now=received+8)
        self.assertTrue(result['success'])
        self.assertEqual(result['due'],'2026-11-03 12:01:50')
        self.assertEqual(self.service.reminders('g','a')[0]['due'],received+60)
        await self.service.deliver_due(now=received+59)
        self.assertEqual(self.sender.calls,[])
        await self.service.deliver_due(now=received+60)
        self.assertEqual(len(self.sender.calls),1)

    async def test_r01_relative_time_units_expiry_and_multiple_times(self):
        expected={'半小时后':1800,'一个半小时之后':5400,'两小时后':7200,'十五分钟后':900,'1.5分钟后':90,'10秒后':10}
        for phrase,seconds in expected.items():
            self.assertEqual(relative_delay(phrase+'提醒我交作业'),seconds)
        self.store.observe(message('expired',text='10秒后提醒我交作业'),now=self.now)
        self.assertFalse(self.service.create_reminder('g','a','expired','2026-11-04 12:00','交作业',now=self.now+11)['success'])
        self.store.observe(message('multiple',text='一分钟后提醒我，五分钟后再提醒我'),now=self.now)
        self.assertFalse(self.service.create_reminder('g','a','multiple','2026-11-04 12:00','交作业',now=self.now)['success'])

    async def test_g01_explicit_games_continue_after_restart_and_are_chat_scoped(self):
        self.store.observe(message('start', text='玩猜谜'), now=self.now)
        result = self.service.game('g', 'a', 'start', 'start', 'riddle')
        self.assertTrue(result['success'])
        reopened = ActivityService(self.store, self.sender)
        self.assertTrue(reopened.game('g', 'a', 'start', 'status')['success'])
        self.store.observe(message('b1', user='b', text='水'), now=self.now+1)
        self.assertFalse(reopened.game('other', 'b', 'b1', 'answer', value='水')['success'])
        self.assertTrue(reopened.game('g', 'b', 'b1', 'answer', value='水')['correct'])
        self.assertFalse(reopened.game('g', 'a', 'start', 'status')['success'])

    async def test_g01_chain_validates_actual_author_answer_and_can_end(self):
        self.store.observe(message('start', text='玩接龙'), now=self.now)
        self.assertTrue(self.service.game('g', 'a', 'start', 'start', 'chain')['success'])
        self.store.observe(message('b1', user='b', text='光芒'), now=self.now+1)
        self.assertTrue(self.service.game('g', 'b', 'b1', 'answer', value='光芒')['success'])
        self.assertFalse(self.service.game('g', 'b', 'b1', 'answer', value='芒果')['success'])
        self.assertFalse(self.service.game('g', 'b', 'b1', 'stop')['success'])
        self.assertTrue(self.service.game('g', 'a', 'start', 'stop')['success'])


if __name__ == '__main__': unittest.main()

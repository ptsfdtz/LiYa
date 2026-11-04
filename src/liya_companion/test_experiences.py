from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from conversation import ConversationStore
from experiences import ExperienceStore, experience_prompt
from personal import PersonalService
from test_conversation import message
from test_personal import Context


class ExperienceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name)/'conversation.db'
        self.conversations = ConversationStore(self.path)
        self.memory = ExperienceStore(self.conversations)

    def tearDown(self):
        self.temp.cleanup()

    def observe(self, mid, text, now=100, **kwargs):
        self.conversations.observe(message(mid,text=text,**kwargs),now=now)

    def test_restart_retains_quote_after_raw_context_expired(self):
        self.observe('first','我下周有一场面试，有点紧张')
        self.assertTrue(self.memory.capture('g','first','concern'))
        self.observe('later','面试结果出来了，我通过了',now=100+100*86400)
        reopened = ExperienceStore(ConversationStore(self.path))
        recalled = reopened.context('g','later')['recalled']
        self.assertEqual(recalled[0]['source_id'],'first')
        self.assertIn('面试',recalled[0]['quote'])

    def test_member_and_session_isolation(self):
        self.observe('first','我明天面试')
        self.memory.capture('g','first','concern')
        self.observe('other','我面试过了',user='b',now=101)
        self.assertEqual(self.memory.context('g','other')['recalled'],[])
        self.assertEqual(self.memory.list('private','a'),[])

    def test_capture_replay_and_ordinary_undirected_messages(self):
        self.observe('first','我明天面试')
        self.assertTrue(self.memory.capture('g','first','concern'))
        self.assertFalse(self.memory.capture('g','first','concern'))
        self.observe('other','我明天考试',is_mentioned=False,now=101)
        self.assertFalse(self.memory.capture('g','other','concern'))

    def test_unrelated_message_does_not_force_recall(self):
        self.observe('first','我明天面试')
        self.memory.capture('g','first','concern')
        self.observe('later','晚上吃什么好',now=101)
        self.assertEqual(self.memory.context('g','later')['recalled'],[])

    def test_ambiguous_continuations_keep_candidates_uncertain(self):
        for i,text in enumerate(('我准备参加面试','我正在准备考试')):
            self.observe(str(i),text,now=100+i)
            self.memory.capture('g',str(i),'concern')
        self.observe('later','终于过了',now=105)
        context=self.memory.context('g','later')
        self.assertEqual(len(context['recalled']),2)
        self.assertTrue(all(r['match']=='possible_continuation' for r in context['recalled']))
        self.assertIn('不能断言',experience_prompt(context))

    def test_forget_erases_quote_and_blocks_replay_and_context(self):
        self.observe('first','我正在准备面试')
        self.memory.capture('g','first','concern')
        episode=self.memory.list('g','a')[0]
        self.conversations.topic('g','a','面试','我正在准备面试','open','first')
        self.observe('forget','请删除我的面试经历',now=101)
        self.assertTrue(self.memory.modify('g','forget','forget',episode['id'])['success'])
        self.assertFalse(self.memory.capture('g','first','concern'))
        self.assertEqual(self.memory.list('g','a'),[])
        self.assertEqual(self.conversations.get_topics('g','a'),[])
        context=self.conversations.context('g','forget')
        self.assertNotIn('我正在准备面试',str(context))
        with self.conversations.connect() as db:
            self.assertEqual(db.execute('SELECT quote FROM experiences').fetchone()[0],'')

    def test_cross_member_and_implicit_deletion_rejected(self):
        self.observe('first','我准备面试')
        self.memory.capture('g','first','concern')
        identity=self.memory.list('g','a')[0]['id']
        self.observe('implicit','你好',now=101)
        self.assertFalse(self.memory.modify('g','implicit','forget',identity)['success'])
        self.observe('other','删除面试经历',user='b',now=102)
        self.assertFalse(self.memory.modify('g','other','forget',identity)['success'])

    async def test_correct_and_list_work_when_native_memory_is_unavailable(self):
        self.observe('first','我下周参加面试')
        self.memory.capture('g','first','concern')
        identity=self.memory.list('g','a')[0]['id']
        self.observe('correct','更正：我是下个月参加面试',now=101)
        ctx=Context(); ctx.result=RuntimeError('offline')
        service=PersonalService(self.conversations,ctx,self.memory)
        result=await service.execute(session='g',source_id='correct',action='correct',target=identity,value='我是下个月参加面试')
        self.assertTrue(result['success'])
        result=await service.execute(session='g',source_id='correct',action='list')
        self.assertFalse(result['facts_available'])
        self.assertEqual(result['experiences'][0]['quote'],'我是下个月参加面试')
        self.assertEqual(len(result['experiences']),1)

    def test_truncation_and_fabricated_correction(self):
        self.observe('first','我参加面试'+('很紧张'*300))
        self.memory.capture('g','first','concern')
        episode=self.memory.list('g','a')[0]
        self.assertEqual(episode['truncated'],1)
        self.assertEqual(len(episode['quote']),800)
        self.observe('correct','更正面试',now=101)
        self.assertFalse(self.memory.modify('g','correct','correct',episode['id'],'我已经通过了')['success'])

    def test_future_experience_cannot_leak_into_earlier_turn(self):
        self.observe('first','我正在准备面试')
        self.observe('future','我面试已经通过了',now=200)
        self.memory.capture('g','future','success')
        self.assertEqual(self.memory.context('g','first')['recalled'],[])


if __name__ == '__main__': unittest.main()

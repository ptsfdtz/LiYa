from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from conversation import ConversationStore, conversation_prompt
from state import StateStore


def message(mid, user='a', text='你好', session='g', **fields):
    return {'session_id': session, 'message_id': mid, 'processed_plain_text': text,
            'is_mentioned': True, 'message_info': {'user_info': {'user_id': user, 'user_nickname': '同名'},
            'group_info': {'group_id': session}}, **fields}


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name)/'conversation.db'
        self.store = ConversationStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_c01_c02_authors_mentions_quotes_and_same_nickname(self):
        self.store.observe(message('a1', text='我不喝咖啡'), now=100)
        self.store.observe(message('b1', user='b', text='他说不喝咖啡', raw_message=[
            {'type': 'reply', 'data': {'target_message_id': 'a1'}},
            {'type': 'at', 'data': {'target_user_id': 'a'}}]), now=101)
        context = self.store.context('g', 'b1')
        self.assertEqual(context['author_id'], 'b')
        self.assertEqual(context['messages'][-1]['quoted'][0]['user'], 'a')
        self.assertEqual(context['messages'][-1]['mentions'], ['a'])
        self.assertIsNone(self.store.get_message('other', 'a1'))
        self.assertIsNone(self.store.get_message('g', 'missing'))

    def test_c02_official_reference_index_is_resolved_only_within_chat(self):
        a = message('a1')
        a['message_info']['additional_config'] = {'qq_official_msg_idx': 'ref1'}
        b = message('b1', user='b')
        b['message_info']['additional_config'] = {'qq_official_ref_msg_idx': 'ref1'}
        self.store.observe(a, now=100)
        self.store.observe(b, now=101)
        self.assertEqual(self.store.context('g', 'b1')['messages'][-1]['quoted'][0]['id'], 'a1')

    def test_c03_continuous_messages_preserve_every_supplement(self):
        for i, text in enumerate(('帮我看看', '这个报错', '重启之后才出现')):
            self.store.observe(message(str(i), text=text), now=100+i)
        self.assertEqual([m['text'] for m in self.store.context('g', '2')['messages']],
                         ['帮我看看', '这个报错', '重启之后才出现'])

    def test_c05_stop_survives_restart_and_cancels_inflight_after_resume(self):
        self.store.observe(message('old'), now=100)
        self.store.observe(message('stop', text='先别说了'), now=101)
        reopened = ConversationStore(self.path)
        self.assertFalse(reopened.may_send('g', 'old'))
        self.assertFalse(reopened.may_send('g', 'stop'))
        self.store.observe(message('other', user='b'), now=102)
        self.assertTrue(self.store.may_send('g', 'other'))
        self.store.observe(message('new', text='璃芽，继续帮我看看'), now=103)
        self.assertTrue(self.store.may_send('g', 'new'))
        self.assertFalse(self.store.may_send('g', 'old'))

    def test_c05_stop_between_other_people_does_not_pause_bot(self):
        self.store.observe(message('stop', text='先别说了', is_mentioned=False), now=100)
        self.assertTrue(self.store.may_send('g', 'stop'))

    def test_m01_preferences_are_scoped_persistent_and_author_verified(self):
        self.store.observe(message('a1', text='以后叫我小林'), now=100)
        self.store.set_preference('g', 'a', 'nickname', '小林', 'a1')
        self.assertEqual(ConversationStore(self.path).get_preferences('g', 'a')['nickname'], '小林')
        self.assertEqual(self.store.get_preferences('g', 'b'), {})
        self.assertEqual(self.store.get_preferences('other', 'a'), {})
        with self.assertRaises(ValueError): self.store.set_preference('g', 'b', 'nickname', '坏值', 'a1')

    def test_t01_topic_resolution_and_stop_do_not_revive_old_worries(self):
        self.store.observe(message('a1', text='作业还没交'), now=100)
        self.store.topic('g', 'a', '作业', '作业还没交', 'open', 'a1')
        self.store.observe(message('a2', text='交好了'), now=101)
        self.store.topic('g', 'a', '作业', '作业已经交好', 'resolved', 'a2')
        self.assertEqual(self.store.get_topics('g', 'a')[0]['status'], 'resolved')
        self.store.topic('g', 'a', '其他', '其他话题', 'open', 'a2')
        self.store.observe(message('stop', text='先别说了'), now=102)
        self.assertEqual([t['key'] for t in self.store.get_topics('g', 'a')], ['作业'])

    def test_v02_only_availability_is_retained_not_binary_media(self):
        self.store.observe(message('pic', is_picture=True, raw_message=[
            {'type': 'image', 'data': 'secret-base64'}]), now=100)
        context = self.store.context('g', 'pic')
        self.assertEqual(context['messages'][0]['media'], ['image'])
        self.assertNotIn('secret-base64', conversation_prompt(context))

    def test_f02_delivery_reservation_survives_restart(self):
        self.assertTrue(self.store.reserve_delivery('g', 'a1', '完整回复'))
        self.assertFalse(ConversationStore(self.path).reserve_delivery('g', 'a1', '完整回复'))
        self.assertTrue(self.store.reserve_delivery('g', 'a2', '完整回复'))

    def test_c01_unknown_reply_target_never_falls_back_to_another_author(self):
        store = StateStore(Path(self.temp.name)/'state.db')
        store.observe('g', 'a', '甲', 'a1', '我是甲', True, now=100)
        self.assertIsNone(store.latest('g', 'unknown'))


if __name__ == '__main__': unittest.main()

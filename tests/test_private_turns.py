"""Private bursts wait for completion; the group timing contract stays separate."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import math
import unittest
from unittest.mock import patch
from test_host_chat_patches import builder, load


class PrivateTurnTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.output = Path(self.temp.name)
        builder.build(Path('/MaiMBot'), self.output)
        self.module = load('src.chat.debounce.liya_private_validation', self.output/'src/chat/debounce/controller.py')
        self.debouncer = self.module.MessageDebouncer()

    def tearDown(self):
        self.temp.cleanup()

    def observe(self, at, text, **kwargs):
        return self.debouncer.observe(author='a', is_bare_image=False, is_pure_emoji=False,
            at_bot=True, received_at=at, wall_time=at, now=at, text=text, is_private=True, **kwargs)

    def test_screenshot_pair_waits_until_after_second_message(self):
        self.observe(0, '我今天上班摸了很久的鱼，感觉什么也不想干')
        self.assertEqual(self.debouncer.deadline, 7)
        self.observe(4, '你今天怎么样')
        self.assertEqual(self.debouncer.deadline, 11)

    def test_long_burst_is_not_cut_off_at_group_batch_or_mention_cap(self):
        for instant in (0, 4, 8, 12, 16):
            self.observe(instant, '这是继续补充的完整一句。')
            self.assertGreater(self.debouncer.deadline, instant)
        self.assertEqual(self.debouncer.deadline, 23)

    def test_unfinished_sentence_waits_longer_and_explicit_hold_needs_new_message(self):
        self.observe(0, '我想说的是：')
        self.assertEqual(self.debouncer.deadline, 12)
        self.observe(5, '我还没说完')
        self.assertTrue(math.isinf(self.debouncer.deadline))
        self.observe(50, '只是今天有点累，没有别的事了。')
        self.assertEqual(self.debouncer.deadline, 57)
        self.observe(52, '我说完了')
        self.assertEqual(self.debouncer.deadline, 53.5)

    def test_new_input_reopens_expired_private_batch_while_generation_is_busy(self):
        self.observe(0, '今天有点累。')
        self.observe(10, '还有刚才忘记说的事。')
        self.assertEqual(self.debouncer.deadline, 17)

    def test_runtime_passes_actual_scope_and_message_to_debouncer(self):
        module = load('src.maisaka.liya_private_runtime_validation', self.output/'src/maisaka/runtime.py')
        native = module.MaisakaHeartFlowChatting
        for group in (True, False):
            runtime = SimpleNamespace(_get_message_debounce_mode=lambda:'natural',
                _message_debounce_was_enabled=True, _fixed_message_debouncer=self.module.MessageDebouncer(),
                chat_stream=SimpleNamespace(is_group_session=group), log_prefix='isolated',
                _get_pending_message_count=lambda:0)
            message = SimpleNamespace(is_notify=False,is_command=False,platform='qq',is_at=True,is_mentioned=True,
                message_id='synthetic',processed_plain_text='我想说的是：',
                message_info=SimpleNamespace(user_info=SimpleNamespace(user_id='a')))
            with patch.object(module,'get_message_shape',return_value=(0,7)), patch.object(module,'is_bot_self',return_value=False), patch.object(module.time,'time',return_value=0), patch.object(module.time,'monotonic',return_value=0):
                native._observe_message_debounce(runtime,message,0)
            self.assertEqual(runtime._fixed_message_debouncer.deadline, 2 if group else 12)

"""Verify scoped native profile paths and unforgeable Host tool context in isolation."""
import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

from src.plugin_runtime.component_query import ComponentQueryService

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('liya_patch_builder',ROOT/'deploy/host/build_patches.py')
builder=importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


class HostChatPatchTests(unittest.IsolatedAsyncioTestCase):
    def test_qq_bot_mentions_follow_unified_identity_without_claiming_other_members(self):
        codec=load('liya_qq_identity_codec',Path('/MaiMBot/plugins/qq_official_adapter/codec.py'))
        payload={'id':'synthetic-message','timestamp':'2026-11-04T11:00:00+08:00',
            'group_openid':'synthetic-group','author':{'member_openid':'synthetic-member'},
            'content':'<@bot-openid> 先别说了 <@other-openid>',
            'mentions':[{'id':'bot-openid','username':'璃芽'},{'id':'other-openid','username':'其他成员'}]}
        decoded,_,_=codec.decode_message('GROUP_MESSAGE_CREATE',payload,'ready-bot-id',
            bot_name='璃芽',unified_account_id='unified-bot',
            id_map={'user':{'bot-openid':'unified-bot','other-openid':'other-member'}})
        targets=[s['data']['target_user_id'] for s in decoded['raw_message'] if s['type']=='at']
        self.assertEqual(targets,['unified-bot','other-member'])
        self.assertEqual(decoded['account_id'],'unified-bot')

    def test_host_overrides_model_supplied_identity_and_profile_paths_are_scoped(self):
        with TemporaryDirectory() as temporary:
            output=Path(temporary)
            hashes=builder.build(Path('/MaiMBot'),output)
            self.assertEqual(len(hashes),8)
            module=load('src.plugin_runtime.liya_component_query_validation',output/'src/plugin_runtime/component_query.py')
            context=SimpleNamespace(stream_id='real-chat',session_id='real-chat',group_id='real-group',
                                    user_id='real-user',platform='qq',metadata={})
            invocation=SimpleNamespace(arguments={'stream_id':'forged-chat','chat_id':'forged-chat',
                'group_id':'forged-group','user_id':'victim','platform':'other','request_id':'real-source'})
            payload=module.ComponentQueryService._build_tool_invocation_payload(
                SimpleNamespace(invoke_method='plugin.invoke_tool'),invocation,context)
            self.assertEqual((payload['stream_id'],payload['user_id'],payload['group_id']),('real-chat','real-user','real-group'))
            self.assertEqual(payload['request_id'],'real-source')
            profile=(output/'src/A_memorix/core/runtime/services/profile_admin_service.py').read_text()
            self.assertIn('return await asyncio.to_thread(scoped_profile, self, person_id, chat_id, limit)',profile)
            self.assertIn('chat_id=anchor_message.session_id',(output/'src/maisaka/memory/person_profile.py').read_text())

    def test_c03_native_debounce_collects_three_directed_supplements(self):
        with TemporaryDirectory() as temporary:
            output=Path(temporary)
            builder.build(Path('/MaiMBot'),output)
            module=load('src.chat.debounce.liya_controller_validation',output/'src/chat/debounce/controller.py')
            debouncer=module.MessageDebouncer()
            for instant in (0.,1.,2.):
                if instant:
                    self.assertGreater(debouncer.deadline,instant)
                debouncer.observe(author='a',is_bare_image=False,is_pure_emoji=False,
                    at_bot=instant==0,received_at=instant,wall_time=instant,now=instant,mode='natural')
            self.assertEqual(debouncer.deadline,4.)

    def test_s01_qq_encoder_preserves_complete_text_in_one_platform_body(self):
        codec=load('liya_qq_codec_validation',Path('/MaiMBot/plugins/qq_official_adapter/codec.py'))
        text='第一句（保留说明）。第二句！\n下一段。'*160
        message={'message_info':{'group_info':{'group_id':'synthetic-group'}},'raw_message':[{'type':'text','data':text}]}
        scene,target,parts,reference=codec.encode_message(message)
        self.assertEqual((scene,target),('group','synthetic-group'))
        self.assertEqual(len(parts),1)
        self.assertEqual(parts[0]['body']['content'],text)

    async def test_f01_model_failure_notifies_once_and_records_actual_send_failure(self):
        bridge=load('liya_failure_bridge_validation',ROOT/'deploy/host/liya_memory.py')
        tool=SimpleNamespace(runtime=SimpleNamespace(session_id='synthetic-group'),
            post_process_reply_message_sequences=lambda text,**kw:['one-sequence'],
            build_success_result=lambda name,text,**kw:{'success':True,**kw},
            build_failure_result=lambda name,text,**kw:{'success':False,**kw})
        with patch('src.maisaka.builtin_tool.reply._invoke_before_post_process_hook',new=AsyncMock(return_value=('失败说明',{}))),patch(
            'src.services.send_service._send_to_target_with_message',new=AsyncMock(return_value=SimpleNamespace(message_id='receipt-only'))) as sender:
            result=await bridge.send_reply_failure(tool,SimpleNamespace(message_id='source'),{})
            self.assertTrue(result['success'])
            self.assertTrue(result['metadata']['generation_failed'])
            sender.assert_awaited_once()
        with patch('src.maisaka.builtin_tool.reply._invoke_before_post_process_hook',new=AsyncMock(return_value=('失败说明',{}))),patch(
            'src.services.send_service._send_to_target_with_message',new=AsyncMock(return_value=None)):
            result=await bridge.send_reply_failure(tool,SimpleNamespace(message_id='source'),{})
            self.assertFalse(result['success'])


if __name__=='__main__': unittest.main()

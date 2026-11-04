"""No external sends: a fake QQ API checks visible quotes and passive credentials separately."""
import importlib.util
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from test_host_chat_patches import load

ROOT = Path(__file__).resolve().parents[1]


class PrivateGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_send_keeps_credentials_without_implicit_quote_and_group_is_preserved(self):
        sys.path.insert(0, '/MaiMBot/plugins')
        import qq_official_adapter
        builder = load('liya_qq_private_patch_builder', ROOT/'deploy/host/build_qq_private_patch.py')
        with TemporaryDirectory() as temp:
            output = Path(temp)/'plugin.py'
            builder.build('/MaiMBot/plugins/qq_official_adapter/plugin.py', output)
            module = load('qq_official_adapter.liya_private_send_validation', output)
            for group in (False, True):
                sender = AsyncMock(return_value={'id':'synthetic-platform-receipt'})
                fake = SimpleNamespace(_ready=True,_id_map={'group':{},'user':{}},
                    _claim_passive_fields=AsyncMock(return_value={'msg_id':'passive-token','msg_seq':1}),
                    _api_post=sender,_store_send_ref_idx=lambda result:None,
                    _build_message_reference=lambda target:{'message_reference':{'message_id':'quote-index'}} if target else {},
                    ctx=SimpleNamespace(logger=logging.getLogger('isolated-qq-quote')))
                info={'additional_config':{'platform_io_target_user_id':'synthetic-user'}}
                if group:info['group_info']={'group_id':'synthetic-group'}
                message={'message_info':info,'reply_to':'source-message','raw_message':[{'type':'text','data':'完整私聊正文'}]}
                result=await module.QQOfficialAdapterPlugin.handle_qq_official_gateway(fake,message)
                self.assertTrue(result['success'])
                body=sender.await_args.args[1]
                self.assertEqual(body['msg_id'],'passive-token')
                self.assertEqual(body['content'],'完整私聊正文')
                self.assertEqual('message_reference' in body,group)

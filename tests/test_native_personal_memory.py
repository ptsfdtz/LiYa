"""Real memory kernel, temporary stores, real configured embedding; no QQ sends."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import json
import sys
from unittest.mock import patch

from src.A_memorix.host_service import AMemorixHostService
from src.A_memorix.core.runtime.sdk_memory_kernel import SDKMemoryKernel

spec = importlib.util.spec_from_file_location('liya_host_memory',Path(__file__).resolve().parents[1]/'deploy/host/liya_memory.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def source(mid, text, user='a', session='test-group-a'):
    return SimpleNamespace(message_id=mid,session_id=session,processed_plain_text=text,platform='qq',
        message_info=SimpleNamespace(user_info=SimpleNamespace(user_id=user),group_info=SimpleNamespace(group_id='test-group')))


class NativePersonalMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_m02_m03_m04_real_memory_scoped_correct_forget_and_failure(self):
        with TemporaryDirectory() as temporary:
            config = AMemorixHostService().get_config()
            config.setdefault('storage',{})['data_dir'] = temporary
            config.setdefault('person_profile',{})['enabled'] = False
            config.setdefault('episode',{})['enabled'] = False
            kernel = SDKMemoryKernel(plugin_root=Path('/MaiMBot/src/A_memorix'),config=config)
            with patch('src.person_info.person_info.resolve_person_id_for_memory',side_effect=lambda platform,user_id:'test-person-'+user_id):
                try:
                    await kernel.initialize()
                    first = source('a1','请记住我不喝咖啡')
                    a = await bridge.operate(kernel,message=first,session='test-group-a',action='remember',value='我不喝咖啡')
                    self.assertTrue(a['success'],a)
                    # Same wording in another chat must have a different paragraph identity.
                    other = await bridge.operate(kernel,message=source('a2','请记住我不喝咖啡'),session='test-group-b',action='remember',value='我不喝咖啡')
                    self.assertTrue(other['success'],other)
                    listed = await bridge.operate(kernel,message=first,session='test-group-a',action='list')
                    self.assertEqual([f['text'] for f in listed['facts']],['我不喝咖啡'])
                    old = listed['facts'][0]['id']
                    foreign = await bridge.operate(kernel,message=source('b1','你记得我什么',user='b'),session='test-group-a',action='list')
                    self.assertEqual(foreign['facts'],[])
                    blocked = await bridge.operate(kernel,message=source('b2','忘掉这件事',user='b'),session='test-group-a',action='forget',target=old)
                    self.assertFalse(blocked['success'])
                    corrected = await bridge.operate(kernel,message=source('a3','你记错了，现在我喝咖啡'),session='test-group-a',action='correct',target=old,value='现在我喝咖啡')
                    self.assertTrue(corrected['success'],corrected)
                    facts = bridge.personal_paragraphs(kernel,'test-person-a','test-group-a')
                    self.assertEqual([f['text'] for f in facts],['现在我喝咖啡'])
                    self.assertEqual([f['text'] for f in bridge.personal_paragraphs(kernel,'test-person-a','test-group-b')],['我不喝咖啡'])
                    # Execute the patched native profile API, not just the bridge helper.
                    spec=importlib.util.spec_from_file_location('liya_profile_patch_builder',Path(__file__).resolve().parents[1]/'deploy/host/build_patches.py')
                    builder=importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(builder)
                    output=Path(temporary)/'host-patches'
                    builder.build(Path('/MaiMBot'),output)
                    sys.modules['src.plugin_runtime.capabilities.liya_memory']=bridge
                    spec=importlib.util.spec_from_file_location('src.A_memorix.core.runtime.services.liya_profile_validation',output/'src/A_memorix/core/runtime/services/profile_admin_service.py')
                    module=importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    service=module.MemoryProfileAdminService(kernel)
                    profile=await service.get_person_profile(person_id='test-person-a',chat_id='test-group-a')
                    self.assertIn('现在我喝咖啡',profile['summary'])
                    self.assertNotIn('我不喝咖啡',profile['summary'])
                    profile=await service.memory_profile_admin(action='query',person_id='test-person-a',chat_id='test-group-b')
                    self.assertIn('我不喝咖啡',profile['summary'])
                    self.assertNotIn('现在我喝咖啡',profile['summary'])
                    forgotten = await bridge.operate(kernel,message=source('a4','别记我喝咖啡这件事'),session='test-group-a',action='forget',target=facts[0]['id'])
                    self.assertTrue(forgotten['success'],forgotten)
                    self.assertEqual(bridge.personal_paragraphs(kernel,'test-person-a','test-group-a'),[])
                    from src.A_memorix.core.runtime.models import KernelSearchRequest
                    recalled=await kernel.search_memory(KernelSearchRequest(query='咖啡',chat_id='test-group-a',person_id='test-person-a',respect_filter=True))
                    self.assertNotIn('我不喝咖啡',json.dumps(recalled,ensure_ascii=False))
                    self.assertNotIn('现在我喝咖啡',json.dumps(recalled,ensure_ascii=False))
                    # Plain pronouns cannot become a broad deletion selector.
                    unclear = await bridge.operate(kernel,message=source('a5','别记这件事'),session='test-group-b',action='forget',target='这件事')
                    self.assertFalse(unclear['success'])
                finally:
                    await kernel.shutdown()


if __name__ == '__main__': unittest.main()

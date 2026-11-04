"""Episodic memory through real SDK hooks; synthetic messages, no QQ sends."""
import json
import unittest
import test_conversation_hooks as contracts


class ExperienceHookTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = contracts.ConversationHookTests.asyncSetUp
    asyncTearDown = contracts.ConversationHookTests.asyncTearDown
    observe = contracts.ConversationHookTests.observe
    async def test_memory_reaches_classifier_planner_and_reply_after_restart(self):
        prompts=[]
        async def classify(prompt,**kwargs):
            prompts.append(prompt)
            return {'success':True,'response':json.dumps({'event':'concern' if len(prompts)==1 else 'success',
                'intensity':0.7,'memorable':True,'memory_ids':[]})}
        self.plugin._ctx.llm.generate=classify
        await self.observe('first','我下周要面试，很紧张')
        await self.plugin.planner(session_id='g')
        self.assertEqual(len(self.plugin.experiences.list('g','a')),1)
        await self.plugin.on_unload()
        await self.plugin.on_load()
        await self.observe('second','我的面试通过了')
        plan=await self.plugin.planner(session_id='g')
        reply=await self.plugin.reply(session_id='g',reply_message_id='second')
        self.assertIn('我下周要面试',prompts[-1])
        self.assertIn('previous',prompts[-1])
        self.assertIn('我下周要面试',plan['modified_kwargs']['items'][-1]['parts'][0]['text'])
        self.assertIn('我下周要面试',reply['modified_kwargs']['extra_prompt'])
        self.assertEqual(len(prompts),2)
        self.assertEqual(len(self.plugin.experiences.list('g','a')),2)

    async def test_failed_classifier_does_not_write_or_block(self):
        async def classify(*args,**kwargs):
            return {'success':False}
        self.plugin._ctx.llm.generate=classify
        await self.observe('first','我下周要面试，很紧张')
        result=await self.plugin.planner(session_id='g')
        self.assertEqual(result['action'],'continue')
        self.assertEqual(self.plugin.experiences.list('g','a'),[])

    async def test_disabled_memory_is_not_injected_or_captured(self):
        await self.observe('first','我下周要面试，很紧张')
        self.plugin.experiences.capture('g','first','concern')
        config=self.plugin.config.model_dump()
        config['plugin']['experience_memory_enabled']=False
        self.plugin.set_plugin_config(config)
        prompts=[]
        async def classify(prompt,**kwargs):
            prompts.append(prompt)
            return {'success':True,'response':'{"event":"success","intensity":0.7,"memorable":true}'}
        self.plugin._ctx.llm.generate=classify
        await self.observe('second','我的面试通过了')
        result=await self.plugin.reply(session_id='g',reply_message_id='second')
        self.assertNotIn('共同经历召回',result['modified_kwargs']['extra_prompt'])
        self.assertIn('"recalled": []',prompts[0])
        self.assertEqual(len(self.plugin.experiences.list('g','a')),1)

    async def test_personal_tool_forgets_experience_and_recall_disappears(self):
        await self.observe('first','我下周要面试，很紧张')
        self.plugin.experiences.capture('g','first','concern')
        identity=self.plugin.experiences.list('g','a')[0]['id']
        self.plugin.store.appraise('g','a','first','concern',0.7)
        async def classify(*args,**kwargs):
            return {'success':True,'response':'{"event":"neutral","intensity":0}'}
        self.plugin._ctx.llm.generate=classify
        await self.observe('forget','请删除我的面试经历')
        await self.plugin.planner(session_id='g')
        async def capability(*args,**kwargs):
            return {'success':True,'facts':[]}
        self.plugin._ctx.call_capability=capability
        listed=await self.plugin.personal_record(stream_id='g',request_id='forget',action='list')
        self.assertTrue(listed['experiences'])
        result=await self.plugin.personal_record(stream_id='g',request_id='forget',action='forget',target=identity)
        self.assertTrue(result['success'])
        self.assertEqual(self.plugin.store.get('g','a')['event'],'neutral')
        reply=await self.plugin.reply(session_id='g',reply_message_id='forget')
        self.assertNotIn('我下周要面试，很紧张',reply['modified_kwargs']['extra_prompt'])

    async def test_model_invented_memory_ids_are_discarded(self):
        async def classify(*args,**kwargs):
            return {'success':True,'response':'{"event":"success","intensity":0.7,"memory_ids":["ep_invented"]}'}
        self.plugin._ctx.llm.generate=classify
        await self.observe('first','我下周要面试，很紧张')
        await self.plugin.planner(session_id='g')
        self.assertEqual(self.plugin.store.get('g','a')['evidence'],'[]')

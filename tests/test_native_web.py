"""Check native search configuration against the installed MaiBot client."""
from pathlib import Path
import os
import sys
import tomllib
import unittest

sys.path.insert(0, os.environ.get('LIYA_PLUGIN_ROOT', str(Path(__file__).resolve().parents[1]/'src')))
from src.config.config import ModelConfig
from src.llm_models.model_client.openai_responses_client import _merge_native_tools


class NativeWebConfigTests(unittest.TestCase):
    def setUp(self):
        path = os.environ.get('LIYA_MODEL_TEMPLATE', str(Path(__file__).resolve().parents[1]/'deploy/config/model_config.example.toml'))
        self.raw = tomllib.loads(Path(path).read_text(encoding='utf-8'))

    def test_real_host_accepts_template_and_limits_native_search_to_reply_model(self):
        config = ModelConfig.model_validate({key:value for key,value in self.raw.items() if key!='inner'})
        models = {model.name:model for model in config.models}
        web = models[config.model_task_config.replyer.model_list[0]]
        self.assertEqual(web.model_identifier, 'gpt-6.1-sol')
        self.assertEqual(web.extra_params['body']['tools'], [{'type':'web_search'}])
        self.assertEqual(web.extra_params['body']['tool_choice'], 'auto')
        for task in ('planner','utils','memory'):
            selected = getattr(config.model_task_config,task).model_list
            for name in selected:
                self.assertNotIn('tools', models[name].extra_params.get('body',{}))

    def test_native_web_search_coexists_with_host_function_tools(self):
        web = next(model for model in self.raw['models'] if model['name']=='dragon-gpt-web')
        extra = dict(web['extra_params']['body'])
        tools = _merge_native_tools(extra, [{'type':'function','name':'reply','parameters':{'type':'object'}}])
        self.assertEqual({tool['type'] for tool in tools}, {'function','web_search'})
        self.assertNotIn('tools', extra)
        self.assertEqual(extra['tool_choice'],'auto')


if __name__ == '__main__':
    unittest.main()

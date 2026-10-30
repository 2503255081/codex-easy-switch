import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from codex_provider_launcher.app import Launcher
from codex_provider_launcher.bridge import rewrite
from codex_provider_launcher import catalog
from codex_provider_launcher.profiles import ProfileStore

class Var:
    def __init__(self, v=''): self.v=v
    def get(self): return self.v
    def set(self, v): self.v=v

class Vault:
    def __init__(self): self.data={}
    def put(self, k, v): self.data[k]=v
    def get(self, k): return self.data.get(k)

class Store:
    def save(self, profiles): self.profiles=profiles

def fixture(mode, url='', model='example-model', plan='按量 API'):
    obj=SimpleNamespace(mode=Var(mode), url=Var(url), key=Var('fixture-key'), plan=Var(plan), visible=Var(True), model_choice=Var(model), effort_choice=Var('跟随当前设置'), key_editing=True, profiles=[], vault=Vault(), store=Store())
    obj.existing=lambda choice: next((p for p in obj.profiles if p.provider == ('custom' if choice == 'relay' else choice)), None)
    return obj

class PresetTests(unittest.TestCase):
    def test_provider_urls_profiles_and_masks(self):
        for plan, suffix in [('按量 API','/api/v3'), ('Coding Plan','/api/coding/v3'), ('Agent Plan','/api/plan/v3')]:
            obj=fixture('doubao',model='endpoint-123',plan=plan)
            p=Launcher.prepare_profile(obj)
            self.assertEqual(p.base_url, 'https://ark.cn-beijing.volces.com'+suffix)
            self.assertEqual((p.kind,p.provider,p.model),('fixed_responses','doubao','endpoint-123'))
            self.assertNotIn('fixture-key',repr(p))
            obj.key_editing=False
            self.assertEqual(Launcher.prepare_profile(obj).id,p.id)
        obj=fixture('qwen',url='llm-fixture',model='qwen-test')
        self.assertEqual(Launcher.prepare_profile(obj).base_url,'https://llm-fixture.cn-beijing.maas.aliyuncs.com/compatible-mode/v1')
        obj=fixture('relay',url='https://example.com/v1',model='my-model')
        obj.visible.set(False)
        p=Launcher.prepare_profile(obj)
        self.assertEqual((p.provider,p.kind,p.visible),('custom','fixed_responses',False))

    def test_custom_model_list_preserves_discovered_catalog_mode(self):
        obj=fixture('relay',url='https://example.com/v1',model='model-1')
        obj.model_details={'model-1': {'service_tiers': []}}
        self.assertEqual(Launcher.prepare_profile(obj).kind, 'responses')

    def test_legacy_doubao_profile_launch_adds_default_candidates_without_mutation(self):
        from codex_provider_launcher.app import doubao_migration_models
        obj=fixture('doubao',model='endpoint-123',plan='Coding Plan')
        p=Launcher.prepare_profile(obj)
        self.assertEqual(p.models,['endpoint-123'])
        p.models=[]  # profiles saved by the previous version
        candidates=doubao_migration_models(p)
        self.assertIn('endpoint-123',candidates)
        self.assertIn('glm-5.3',candidates)
        self.assertEqual(p.models,[])
        p.models=['endpoint-123']  # explicit single selection remains single
        self.assertEqual(doubao_migration_models(p),['endpoint-123'])

    def test_deepseek_supports_configured_default_model(self):
        obj=fixture('deepseek',model='deepseek-v4-pro')
        p=Launcher.prepare_profile(obj)
        self.assertEqual((p.kind,p.model),('deepseek','deepseek-v4-pro'))
        data={'method':'turn/start','params':{'model':'deepseek-flash'}}
        got=json.loads(rewrite(json.dumps(data).encode(),'launcher_custom','deepseek-v4-pro',['deepseek-flash','deepseek-v4-pro']))
        self.assertEqual(got['params']['model'],'deepseek-flash')

    def test_bailian_multiple_models_saved(self):
        obj=fixture('qwen',url='llm-fixture',model='qwen3.8-max')
        obj.more_models=Var('qwen3.8-flash, glm-5.3')
        p=Launcher.prepare_profile(obj)
        self.assertEqual(p.models,['qwen3.8-max','qwen3.8-flash','glm-5.3'])

    def test_bailian_legacy_profile_launch_gets_candidates_without_mutating(self):
        from codex_provider_launcher.app import Launcher
        obj=fixture('qwen',url='',model='qwen3.8-max')
        p=Launcher.prepare_profile(obj)
        p.models=[]
        obj.busy=False
        seen=[]
        obj.start=lambda active: seen.append(active)
        Launcher.launch_saved(obj,p)
        self.assertIn('qwen3.8-flash',seen[0].models)
        self.assertEqual(p.models,[])
        p.models=['qwen3.8-max']
        Launcher.launch_saved(obj,p)
        self.assertEqual(seen[-1].models,['qwen3.8-max'])

    def test_bailian_permissions_uses_documented_endpoint_and_filters(self):
        import urllib.parse
        from unittest.mock import MagicMock
        result={'success':True,'output':{'total':3,'permissions':[
            {'model':'qwen3.8-max','permissions':{'inference':True}},
            {'model':'qwen3.8-flash','permissions':{'inference':False}},
            {'model':'image-only','permissions':{'inference':True}}]}}
        response=MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps(result).encode()
        opener=MagicMock()
        opener.open.return_value=response
        with patch.object(catalog.urllib.request,'build_opener',return_value=opener):
            models=catalog.list_bailian_authorized('https://llm-abc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1','fixture-secret',('qwen3.8-max','qwen3.8-flash'))
        self.assertEqual(models,['qwen3.8-max'])
        req=opener.open.call_args.args[0]
        self.assertEqual(urllib.parse.urlsplit(req.full_url).path,'/api/v1/models/permissions')
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)['authorization_scope'],['AUTHORIZED'])
        self.assertEqual(req.get_header('Authorization'),'Bearer fixture-secret')
        with self.assertRaises(ValueError): catalog.list_bailian_authorized('https://evil.example/compatible-mode/v1','fixture-secret',())

    def test_bailian_default_url_and_qianwen_are_separate(self):
        obj=fixture('qwen',url='',model='qwen3.8-max')
        p=Launcher.prepare_profile(obj)
        self.assertEqual(p.base_url,'https://dashscope.aliyuncs.com/compatible-mode/v1')
        self.assertEqual(p.provider,'qwen')

    def test_qianwen_plan_urls_secrets_and_account_types(self):
        obj=fixture('qianwen',model='qwen3.8-max')
        obj.qianwen_plan=Var('按量 API')
        obj.more_models=Var('qwen3.8-flash')
        obj.key.set('sk-ws-fixture')
        p=Launcher.prepare_profile(obj)
        self.assertEqual((p.provider,p.base_url,p.models),('qianwen','https://maas.qianwenaiapi.com/compatible-mode/v1',['qwen3.8-max','qwen3.8-flash']))
        other=fixture('qianwen',model='qwen3.8-max')
        other.qianwen_plan=Var('Token Plan')
        other.key.set('sk-sp-fixture')
        p=Launcher.prepare_profile(other)
        self.assertEqual(p.base_url,'https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1')
        other.qianwen_plan.set('按量 API')
        other.key.set('sk-sp-fixture')
        with self.assertRaisesRegex(ValueError,'Token Plan'): Launcher.connection_details(other)
        wrong=fixture('qianwen',model='qwen3.8-max')
        wrong.qianwen_plan=Var('Token Plan')
        wrong.key.set('sk-ws-fixture')
        with self.assertRaisesRegex(ValueError,'按量'): Launcher.connection_details(wrong)

    def test_provider_key_links_are_static_and_not_custom(self):
        from codex_provider_launcher.app import PROVIDER_KEY_URLS
        self.assertEqual(PROVIDER_KEY_URLS['qianwen'],'https://platform.qianwenai.com/home/api-keys')
        self.assertIn('bailian.console.aliyun.com',PROVIDER_KEY_URLS['qwen'])
        self.assertIn('ark.volcengine.com',PROVIDER_KEY_URLS['doubao'])
        self.assertIn('platform.deepseek.com',PROVIDER_KEY_URLS['deepseek'])
        self.assertNotIn('relay',PROVIDER_KEY_URLS)

    def test_doubao_model_list_preserved_and_validated(self):
        obj=fixture('doubao',model='doubao-seed-a')
        obj.more_models=Var('doubao-seed-b, doubao-seed-c, doubao-seed-b')
        p=Launcher.prepare_profile(obj)
        self.assertEqual(p.models,['doubao-seed-a','doubao-seed-b','doubao-seed-c'])
        obj.more_models.set('invalid?model')
        with self.assertRaises(ValueError): Launcher.prepare_profile(obj)

    def test_personal_custom_button_name(self):
        obj=fixture('relay',url='https://example.com/v1',model='api-model')
        obj.display_name=Var('我的通道')
        p=Launcher.prepare_profile(obj)
        self.assertEqual((p.name,p.model),('我的通道','api-model'))
        obj.display_name.set('')
        with self.assertRaises(ValueError): Launcher.prepare_profile(obj)

    def test_bridge_keeps_selected_allowed_model(self):
        allowed=['doubao-a','doubao-b']
        for method in ('thread/start','thread/resume','turn/start'):
            data={'method':method,'params':{'model':'doubao-b','config':{'model':'old'}}}
            result=json.loads(rewrite(json.dumps(data).encode(),'launcher_custom','doubao-a',allowed))
            self.assertEqual(result['params']['model'],'doubao-b')
            self.assertEqual(result['params']['config']['model'],'doubao-b')
        data={'method':'turn/start','params':{'model':'foreign-model'}}
        self.assertEqual(json.loads(rewrite(json.dumps(data).encode(),'launcher_custom','doubao-a',allowed))['params']['model'],'doubao-a')
        with self.assertRaises(ValueError): rewrite(json.dumps(data).encode(),'launcher_custom','doubao-a',['bad model'])

    def test_paid_response_test_sends_one_responses_request(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from codex_provider_launcher.profiles import new_profile
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                seen.append((self.path,self.headers.get('Authorization'),body))
                raw=json.dumps({'status':'completed','output':[{'type':'message'}]}).encode()
                self.send_response(200); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            p=new_profile('test','fixed_responses',f'http://127.0.0.1:{server.server_port}/v1','model-1')
            catalog.test_responses(p,'fixture-only-key')
            self.assertEqual((seen[0][0],seen[0][1],seen[0][2]['model']),('/v1/responses','Bearer fixture-only-key','model-1'))
        finally:
            server.shutdown(); server.server_close()

    def test_legacy_profile_load_preserves_credentials_identity(self):
        import uuid
        with tempfile.TemporaryDirectory() as d:
            pid=str(uuid.uuid4())
            Path(d,'profiles.json').write_text(json.dumps({'version':1,'profiles':[{'id':pid,'name':'old','kind':'responses','base_url':'https://example.com/v1','model':'auto'}]}),encoding='utf-8')
            p=ProfileStore(Path(d)).load()[0]
            self.assertEqual((p.id,p.provider,p.visible),(pid,'',True))

    def test_fixed_model_replaces_existing_thread_model(self):
        for method in ('thread/start','thread/resume','turn/start'):
            data={'method':method,'params':{'model':'gpt-old','config':{'model':'gpt-old'}}}
            result=json.loads(rewrite(json.dumps(data).encode(),'launcher_custom','endpoint-123'))
            self.assertEqual(result['params']['model'],'endpoint-123')
            self.assertEqual(result['params']['config']['model'],'endpoint-123')

    def test_generated_catalog_keeps_required_template_fields(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d)
            (home/'models_cache.json').write_text(json.dumps({'models':[{'slug':'base','visibility':'list','truncation_policy':{'mode':'tokens','limit':100},'experimental_supported_tools':[]}]}))
            with patch.dict('os.environ',{'CODEX_HOME':str(home)}), patch.object(catalog,'data_dir',return_value=home/'launcher'):
                path,models=catalog.fixed_catalog('endpoint-123','豆包',['endpoint-123','doubao-b'])
            self.assertEqual([m['slug'] for m in models],['endpoint-123','doubao-b'])
            self.assertIn('truncation_policy',json.loads(path.read_text(encoding='utf-8'))['models'][0])

    def test_reasoning_capabilities_are_model_and_provider_specific(self):
        self.assertEqual(catalog.reasoning_levels('qianwen', 'qwen3.8-max'), ('none', 'low', 'medium', 'xhigh'))
        self.assertEqual(catalog.reasoning_levels('qwen', 'glm-5.2'), ('none', 'high', 'max'))
        self.assertEqual(catalog.reasoning_levels('doubao', 'doubao-seed-2-1-pro-260915'), ('minimal', 'low', 'medium', 'high'))
        self.assertEqual(catalog.reasoning_levels('doubao', 'qwen3.8-max'), ('low',))
        self.assertEqual(catalog.reasoning_levels('qianwen', 'unknown'), ('low',))
        with tempfile.TemporaryDirectory() as d:
            home=Path(d)
            (home/'models_cache.json').write_text(json.dumps({'models':[{'slug':'base','visibility':'list'}]}))
            with patch.dict('os.environ',{'CODEX_HOME':str(home)}), patch.object(catalog,'data_dir',return_value=home/'launcher'):
                _, models = catalog.fixed_catalog('qwen3.8-max', '千问', ['glm-5.2','unknown'], 'qianwen')
            self.assertEqual([[x['effort'] for x in m['supported_reasoning_levels']] for m in models],
                             [['none','low','medium','xhigh'], ['none','high','max'], ['low']])
            self.assertEqual(models[1]['default_reasoning_level'], 'high')

if __name__=='__main__': unittest.main()

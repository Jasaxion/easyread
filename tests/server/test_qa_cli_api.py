import base64
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from easyread.app import config
from easyread.server import qa_cli_api, settings_api


class Handler:
    command = 'POST'
    def __init__(self, path, body=None, origin=True):
        self.path=path;self.data=json.dumps(body or {}).encode()
        self.headers={'Host':'localhost:8765','Content-Length':str(len(self.data))}
        if origin:self.headers['Origin']='http://localhost:8765'
        self.close_connection=False
    def _body(self):return self.data
    def _json(self,status,body):self.result=(status,body)


class QaApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)
        self.patches=[patch.object(config,'HOME',self.home/'app'),patch.object(config,'CONFIG_PATH',self.home/'app/config.json'),patch('pathlib.Path.home',return_value=self.home/'native'),patch.dict(os.environ,{'CODEX_HOME':str(self.home/'native/.codex'),'CLAUDE_CONFIG_DIR':str(self.home/'native/.claude'),'PI_CODING_AGENT_DIR':str(self.home/'native/.pi/agent')})]
        for p in self.patches:p.start()
        (self.home/'app').mkdir();(self.home/'native').mkdir()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def test_pi_api_policy_model_import_and_honest_capabilities(self):
        config.save({'chat': {'models': [{'id': 'pi-answer', 'engine': 'pi', 'model': 'provider/model'}]}})
        h = Handler('/api/chat/cli/pi/models/pi-answer', {'mode': 'full'})
        self.assertTrue(qa_cli_api.handle(h))
        self.assertEqual(h.result[0], 200)
        listing = h.result[1]
        self.assertEqual(listing['tools'], ['read', 'grep', 'find', 'ls'])
        self.assertFalse(listing['capabilities']['sandbox'])
        self.assertFalse(listing['capabilities']['tool_approval'])
        self.assertTrue(listing['limitations'])
        self.assertTrue(listing['native']['path'].endswith('.pi/agent/settings.json'))
        document = '---\nname: pi-review\ndescription: Review a paper\n---\nRead it.\n'
        upload = Handler('/api/chat/cli/pi/models/pi-answer/skills/import',
                         {'filename': 'SKILL.md', 'content_b64': base64.b64encode(document.encode()).decode()})
        qa_cli_api.handle(upload)
        item = upload.result[1]['skills'][0]
        self.assertTrue(item['managed'])
        qa_cli_api.handle(Handler('/api/chat/cli/pi/models/pi-answer', {'mode': 'full', 'skill_overrides': {item['id']: False}}))
        self.assertFalse(qa_cli_api.listing('pi', 'pi-answer')['skills'][0]['enabled'])
        self.assertEqual(qa_cli_api.listing('pi')['settings']['mode'], 'default')
        qa_cli_api.handle(Handler('/api/chat/cli/pi/models/pi-answer', {'inherit': True}))
        self.assertTrue(qa_cli_api.listing('pi', 'pi-answer')['inherit'])
        h = Handler('/api/chat/cli/pi', {'mode': 'full'}, origin=False)
        qa_cli_api.handle(h)
        self.assertEqual(h.result[0], 403)
        for policy in ({'mode': 'custom', 'mcp': True}, {'mode': 'custom', 'web_search': True},
                       {'mode': 'full', 'skill_overrides': {'native:a': False}}):
            with self.assertRaises(ValueError):
                qa_cli_api.handle(Handler('/api/chat/cli/pi', policy))

    def test_settings_save_only_selected_qa_engine(self):
        config.save({'engine':'codex','codex':{'model':'keep-model','extra_args':['--example']},'qa_cli':{'claude':{'mode':'default'}}})
        before=config.load()
        h=Handler('/api/chat/cli/codex',{'mode':'full'})
        self.assertTrue(qa_cli_api.handle(h));self.assertEqual(h.result[0],200)
        after=config.load()
        self.assertEqual(after['codex'],before['codex']);self.assertEqual(after['engine'],before['engine'])
        self.assertEqual(after['qa_cli']['claude'],before['qa_cli']['claude'])
        self.assertEqual(after['qa_cli']['codex']['mode'],'full')
        with self.assertRaises(ValueError):settings_api.save_config({'qa_cli':{'codex':{'mode':'full'}}})

    def test_requires_same_origin_and_rejects_unknown_fields(self):
        h=Handler('/api/chat/cli/claude',{'mode':'full'},origin=False)
        qa_cli_api.handle(h);self.assertEqual(h.result[0],403)
        with self.assertRaises(ValueError):qa_cli_api.handle(Handler('/api/chat/cli/claude',{'mode':'full','dangerously_skip_permissions':True}))
        self.assertFalse(config.CONFIG_PATH.exists())

    def model_config(self):
        models=[{'id':'translate-a','engine':'codex','model':'model-a','name':'Translator'},
                {'id':'answer-b','engine':'codex','model':'model-b','name':'Answerer'}]
        config.save({'engine':'codex','codex':{'model':'model-a'},'chat':{'models':models,'default':'answer-b'},
                     'qa_cli':{'codex':{'mode':'default'}}})
        return models

    def test_model_policy_is_independent_and_inheritance_is_explicit(self):
        self.model_config(); before=config.load()
        h=Handler('/api/chat/cli/codex/models/answer-b',{'mode':'full'})
        qa_cli_api.handle(h); self.assertEqual(h.result[0],200)
        self.assertEqual(h.result[1]['model_id'],'answer-b'); self.assertFalse(h.result[1]['inherit'])
        after=config.load()
        self.assertEqual(after['qa_cli'],before['qa_cli'])
        self.assertEqual(after['codex'],before['codex']); self.assertEqual(after['chat'],before['chat'])
        self.assertEqual(qa_cli_api.listing('codex','translate-a')['settings']['mode'],'default')
        self.assertTrue(qa_cli_api.listing('codex','translate-a')['inherit'])
        # An explicit lean entry does not inherit a later global full policy.
        qa_cli_api.handle(Handler('/api/chat/cli/codex/models/translate-a',{'mode':'default'}))
        qa_cli_api.handle(Handler('/api/chat/cli/codex',{'mode':'full'}))
        self.assertEqual(qa_cli_api.listing('codex','translate-a')['settings']['mode'],'default')
        self.assertEqual(qa_cli_api.listing('codex','answer-b')['settings']['mode'],'full')
        qa_cli_api.handle(Handler('/api/chat/cli/codex/models/translate-a',{'inherit':True}))
        self.assertTrue(qa_cli_api.listing('codex','translate-a')['inherit'])
        self.assertEqual(qa_cli_api.listing('codex','translate-a')['settings']['mode'],'full')
        self.assertNotIn('translate-a',config.load()['qa_model_policies'])

    def test_model_policy_requires_known_matching_entry_and_same_origin(self):
        self.model_config(); before=config.CONFIG_PATH.read_bytes()
        for url in ('/api/chat/cli/codex/models/missing','/api/chat/cli/claude/models/answer-b'):
            h=Handler(url,{'mode':'full'});qa_cli_api.handle(h)
            self.assertEqual(h.result[0],404)
        h=Handler('/api/chat/cli/codex/models/answer-b',{'mode':'full'},origin=False)
        qa_cli_api.handle(h);self.assertEqual(h.result[0],403)
        for value in (1,False,'true'):
            with self.assertRaises(ValueError):qa_cli_api.handle(Handler('/api/chat/cli/codex/models/answer-b',{'inherit':value}))
        self.assertEqual(config.CONFIG_PATH.read_bytes(),before)
        with self.assertRaises(ValueError):settings_api.save_config({'qa_model_policies':{'answer-b':{'mode':'full'}}})
        with self.assertRaises(ValueError):settings_api.save_config({'_qa_model_id':'answer-b'})

    def test_model_crud_preserves_other_policies_and_prunes_deleted_or_changed_entries(self):
        models=self.model_config()
        qa_cli_api.handle(Handler('/api/chat/cli/codex/models/answer-b',{'mode':'full'}))
        qa_cli_api.handle(Handler('/api/chat/cli/codex/models/translate-a',{'mode':'custom','allowed_tools':[]}))
        config.save({'chat':{'models':[{**models[0],'name':'Renamed'},models[1]]}})
        self.assertEqual(qa_cli_api.listing('codex','answer-b')['settings']['mode'],'full')
        config.save({'chat':{'models':[models[0]]}})
        self.assertNotIn('answer-b',config.load()['qa_model_policies'])
        config.save({'chat':{'models':models}})
        self.assertTrue(qa_cli_api.listing('codex','answer-b')['inherit'])
        config.save({'chat':{'models':[{**models[0],'engine':'claude'},models[1]]}})
        self.assertNotIn('translate-a',config.load()['qa_model_policies'])
        self.assertTrue(qa_cli_api.listing('claude','translate-a')['inherit'])

    def test_append_synthesized_entry_preserves_translation_and_latest_models(self):
        from easyread.chat import chat_models
        self.model_config()
        config.save({"engine":"none","auto_translate":False})
        qa_cli_api.handle(Handler("/api/chat/cli/codex/models/answer-b", {"mode":"full"}))
        before=config.load()
        entry={"id":"legacy-translator","engine":"codex","model":"legacy-model","name":"Legacy", "_unsaved":True}
        with patch.object(chat_models,"listing",side_effect=lambda cfg:{"models":chat_models.models(cfg)}):
            settings_api.save_chat_models({"append":entry})
            settings_api.save_chat_models({"append":entry})
            with self.assertRaises(ValueError): settings_api.save_chat_models({"append":{**entry,"model":"different"}})
        after=config.load()
        self.assertEqual(after["engine"],"none");self.assertFalse(after["auto_translate"])
        self.assertEqual(after["codex"],before["codex"])
        self.assertEqual(after["qa_model_policies"],before["qa_model_policies"])
        self.assertEqual([m["id"] for m in after["chat"]["models"]],["translate-a","answer-b","legacy-translator"])
        self.assertNotIn("_unsaved",after["chat"]["models"][-1])

    def test_entry_skill_overrides_do_not_modify_sibling_policy(self):
        self.model_config()
        qa_cli_api.handle(Handler('/api/chat/cli/codex/models/answer-b',{'mode':'full','skill_overrides':{'native:demo':False}}))
        qa_cli_api.handle(Handler('/api/chat/cli/codex/models/translate-a',{'mode':'custom','skills':True,'skill_overrides':{'native:demo':True}}))
        self.assertFalse(config.load()['qa_model_policies']['answer-b']['settings']['skill_overrides']['native:demo'])
        self.assertTrue(config.load()['qa_model_policies']['translate-a']['settings']['skill_overrides']['native:demo'])
        qa_cli_api.handle(Handler('/api/chat/cli/codex/models/answer-b',{'inherit':True}))
        self.assertIn('translate-a',config.load()['qa_model_policies'])

    def test_import_inert_skill_is_qa_only_and_survives_policy_changes(self):
        document='---\nname: paper-review\ndescription: Review a paper\n---\nUse the requested review checklist.\n'
        h=Handler('/api/chat/cli/codex/skills/import',{'filename':'SKILL.md','content_b64':base64.b64encode(document.encode()).decode()})
        qa_cli_api.handle(h);self.assertEqual(h.result[0],200)
        item=h.result[1]['skills'][0]
        self.assertTrue(item['managed']);self.assertTrue(Path(item['path']).is_relative_to(config.HOME/'qa-skills/codex'))
        self.assertFalse((self.home/'native/.agents/skills').exists())
        self.assertFalse((self.home/'native/.codex/skills').exists())
        q=Handler('/api/chat/cli/codex',{'mode':'full','skill_overrides':{item['id']:False}})
        qa_cli_api.handle(q)
        self.assertFalse(q.result[1]['skills'][0]['enabled'])
        self.assertEqual(Path(item['path']).read_text(),document)
        q=Handler('/api/chat/cli/codex',{'mode':'full','skill_overrides':{}})
        qa_cli_api.handle(q)
        self.assertEqual(config.load()['qa_cli']['codex']['skill_overrides'],{})
        self.assertTrue(q.result[1]['skills'][0]['enabled'])

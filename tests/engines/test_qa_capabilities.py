import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from easyread.chat import chat
from easyread.engines import engines, qa_capabilities as qa


class PolicyTest(unittest.TestCase):
    def config(self, engine, mode='default', **extra):
        return {'engine':engine, engine:{'model':'test-model'}, 'qa_cli':{engine:{'mode':mode, **extra}}}

    def test_default_is_unchanged_and_engine_settings_are_independent(self):
        cfg=self.config('codex')
        self.assertEqual(qa.prepare(cfg),cfg)
        cfg['qa_cli']['claude']={'mode':'full'}
        self.assertEqual(qa.prepare(cfg),cfg)
        self.assertEqual(qa.policy(cfg,'claude')['mode'],'full')

    def test_translation_strips_qa_markers_and_keeps_original_limits(self):
        for engine in qa.ENGINES:
            cfg=self.config(engine,'full')
            before=copy.deepcopy(cfg)
            prepared=qa.prepare(cfg)
            self.assertFalse(prepared[engine]['lean'])
            translated=engines.for_translation(prepared)
            self.assertTrue(translated[engine]['lean'])
            self.assertNotIn('_qa_policy',translated[engine])
            self.assertNotIn('_chat',translated[engine])
            self.assertEqual(cfg,before)

    def test_validates_native_tools_and_does_not_offer_bypass(self):
        with self.assertRaises(ValueError):qa.validate('codex',{'allowed_tools':['Bash']})
        with self.assertRaises(ValueError):qa.validate('claude',{'allowed_tools':['--dangerously-skip-permissions']})
        with self.assertRaises(ValueError):qa.validate('codex',{'sandbox':'danger-full-access'})
        with self.assertRaises(ValueError):qa.validate('claude',{'skills':'yes'})
        self.assertEqual(qa.validate('claude',{'allowed_tools':['Read','Bash(git status *)']})['allowed_tools'],['Read','Bash(git status *)'])

    def test_full_inherits_native_capabilities_without_forcing_permissions(self):
        for engine in qa.ENGINES:
            selected=qa.policy(self.config(engine,'full'),engine)
            self.assertEqual(qa.claude_args(selected) if engine=='claude' else qa.codex_args(selected),[])

    def test_custom_claude_maps_tools_skills_web_and_mcp(self):
        selected=qa.validate('claude',{'mode':'custom','skills':True,'mcp':False,'web_search':True,'web_fetch':False,'allowed_tools':['Read','Bash(git status *)']})
        args=qa.claude_args(selected)
        self.assertEqual(set(args[args.index('--tools')+1].split(',')),{'Read','Bash','Skill','WebSearch'})
        self.assertIn('Bash(git status *)',args[args.index('--allowedTools')+1])
        self.assertIn('--strict-mcp-config',args)
        self.assertIn('WebFetch',args[args.index('--disallowedTools')+1])

    def test_custom_codex_uses_real_flags_and_preserves_mcp_config_file(self):
        selected=qa.validate('codex',{'mode':'custom','skills':False,'mcp':False,'web_search':False,'allowed_tools':['view_image'],'sandbox':'read-only'})
        with patch.object(qa,'_native',return_value={'mcp_servers':{'research':{'command':'unchanged'}}}):args=qa.codex_args(selected)
        self.assertIn('features.shell_tool=false',args)
        self.assertIn('web_search="disabled"',args)
        self.assertIn('mcp_servers."research".enabled=false',args)
        self.assertEqual(args[args.index('--sandbox')+1],'read-only')
        self.assertNotIn('--allowedTools',args)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',args)

    def test_codex_mcp_off_uses_effective_profile_and_project_settings(self):
        from easyread.app import native_cli_settings
        selected=qa.validate('codex',{'mode':'custom','mcp':False})
        with patch.object(native_cli_settings,'codex_effective',return_value={'mcp_servers':{'user':{},'project':{}}}) as read:
            args=qa.codex_args(selected,Path('/work/paper'),'codex-path')
        read.assert_called_once_with(Path('/work/paper'),'codex-path')
        self.assertIn('mcp_servers."project".enabled=false',args)
        with patch.object(native_cli_settings,'codex_effective',side_effect=ValueError('unsupported')):
            with self.assertRaises(ValueError):qa.codex_args(selected,Path('/work/paper'))

    def test_instruction_only_codex_skill_does_not_require_enabling_shell(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'SKILL.md';path.write_text('Use a three-part paper review.')
            item={'id':'managed:inspect','name':'inspect','description':'Review','path':str(path),'managed':True}
            selected=qa.validate('codex',{'mode':'custom','skills':True,'allowed_tools':[]})
            with patch.object(qa,'_skills',return_value=[item]):
                with qa.invocation('codex',selected,'question') as (args,prompt):
                    self.assertIn('Use a three-part paper review.',prompt)
                    self.assertNotIn('features.shell_tool=true',args)

    def test_skill_native_settings_changes_are_read_each_time(self):
        selected=qa.validate('claude',{'mode':'full'})
        item={'id':'native:a','name':'inspect','path':'/test/inspect/SKILL.md','managed':False}
        with patch.object(qa,'_native',return_value={'skillOverrides':{'inspect':'off'}}):self.assertFalse(qa.skill_enabled('claude',item,selected))
        with patch.object(qa,'_native',return_value={'skillOverrides':{'inspect':'on'}}):self.assertTrue(qa.skill_enabled('claude',item,selected))
        selected['skill_overrides']['native:a']=False
        with patch.object(qa,'_native',return_value={}):self.assertFalse(qa.skill_enabled('claude',item,selected))

    def test_managed_claude_skills_are_session_only_and_unselected_omitted(self):
        with tempfile.TemporaryDirectory() as folder:
            skill=Path(folder)/'inspect';skill.mkdir();(skill/'SKILL.md').write_text('---\nname: inspect\ndescription: Inspect a paper\n---\nFollow this workflow.')
            item={'id':'managed:inspect','name':'inspect','description':'Inspect a paper','path':str(skill/'SKILL.md'),'managed':True}
            with patch.object(qa,'_skills',return_value=[item]):
                selected=qa.validate('claude',{'mode':'full'})
                with qa.invocation('claude',selected,'question') as (args,prompt):
                    plugin=Path(args[args.index('--plugin-dir')+1]);self.assertTrue((plugin/'skills/inspect/SKILL.md').is_file())
                    self.assertEqual(prompt,'question')
                self.assertFalse(plugin.exists())
                selected['skill_overrides']['managed:inspect']=False
                with qa.invocation('claude',selected,'question') as (args,_):self.assertNotIn('--plugin-dir',args)
            self.assertTrue((skill/'SKILL.md').exists())

    def test_codex_managed_imports_use_qa_catalog_not_fictional_flags(self):
        item={'id':'managed:inspect','name':'inspect','description':'Review methodology','path':'/qa/inspect/SKILL.md','managed':True}
        with patch.object(qa,'_skills',return_value=[item]):
            with qa.invocation('codex',qa.validate('codex',{'mode':'full'}),'$inspect the paper') as (args,prompt):
                self.assertIn('/qa/inspect/SKILL.md',prompt);self.assertIn('Q&A skills',prompt)
                self.assertNotIn('extra_roots',' '.join(args));self.assertNotIn('CODEX_HOME',' '.join(args))


class PerModelPolicyTest(unittest.TestCase):
    def config(self):
        return {'engine':'codex','codex':{'model':'model-a'},
                'chat':{'default':'answer-b','models':[
                    {'id':'translate-a','engine':'codex','model':'model-a','name':'Lean model'},
                    {'id':'answer-b','engine':'codex','model':'model-b','name':'Full model'}]},
                'qa_cli':{'codex':{'mode':'full'}},
                'qa_model_policies':{
                    'translate-a':{'engine':'codex','settings':{'mode':'default'}},
                    'answer-b':{'engine':'codex','settings':{'mode':'full'}}}}

    def test_model_selection_is_metadata_until_explicit_qa_prepare(self):
        from easyread.chat import chat_models
        cfg=self.config(); before=copy.deepcopy(cfg)
        lean,_=chat_models.engine_cfg(cfg,'translate-a'); full,_=chat_models.engine_cfg(cfg,'answer-b')
        self.assertEqual(lean['codex']['model'],'model-a')
        self.assertEqual(full['codex']['model'],'model-b')
        self.assertNotIn('_qa_policy',full['codex'])
        self.assertNotIn('_qa_policy',qa.prepare(lean)['codex'])
        expanded=qa.prepare(full)
        self.assertEqual(expanded['codex']['_qa_policy']['mode'],'full')
        translated=engines.for_translation(expanded)
        self.assertTrue(translated['codex']['lean']); self.assertEqual(translated['codex']['model'],'model-b')
        self.assertNotIn('_qa_policy',translated['codex']); self.assertNotIn('_qa_model_id',translated)
        expanded['_qa_model_id']='translate-a'
        narrowed=qa.prepare(expanded)
        self.assertNotIn('_qa_policy',narrowed['codex']); self.assertTrue(narrowed['codex']['lean'])
        self.assertEqual(cfg,before)

    def test_two_codex_questions_use_different_models_and_capability_modes(self):
        from easyread.chat import chat_models
        cfg=self.config(); seen=[]
        with patch.object(chat.netcheck,'problem',return_value=None), patch.object(engines,'run',side_effect=lambda options,*a,**k: seen.append(options) or 'answer'):
            for mid in ('translate-a','answer-b'):
                selected,_=chat_models.engine_cfg(cfg,mid)
                list(chat.stream(selected,'question',Path('.'),threading.Event(),qa=True))
        self.assertEqual([s['codex']['model'] for s in seen],['model-a','model-b'])
        self.assertTrue(seen[0]['codex']['lean']);self.assertNotIn('_qa_policy',seen[0]['codex'])
        self.assertFalse(seen[1]['codex']['lean']);self.assertEqual(seen[1]['codex']['_qa_policy']['mode'],'full')

    def test_unknown_model_and_invalid_policy_never_fall_back_to_full(self):
        cfg=self.config();cfg['_qa_model_id']='missing'
        with self.assertRaises(qa.ModelPolicyNotFound):qa.prepare(cfg)
        cfg['_qa_model_id']='answer-b';cfg['qa_model_policies']['answer-b']['settings']=None
        with self.assertRaises(ValueError):qa.prepare(cfg)

    def test_stale_explicit_qa_selection_never_falls_back_to_another_full_entry(self):
        from easyread.chat import chat_models, library_search
        from easyread.server import chat_handler
        from unittest.mock import Mock
        cfg=self.config()
        for invalid in ('deleted-entry',False,42):
            with self.assertRaises(ValueError):chat_models.require_qa_model(cfg,invalid)
        chat_models.require_qa_model(cfg,None);chat_models.require_qa_model(cfg,'')
        with patch.object(chat_handler.config,'load',return_value=cfg), patch.object(chat_handler.chat_store,'get',return_value=None), patch.object(chat_models,'engine_cfg') as choose:
            with self.assertRaises(ValueError):chat_handler.handle(Mock(),Mock(),{'text':'question','model':'deleted-entry'})
            choose.assert_not_called()
        index=Mock();index.snapshot.return_value=([],[])
        with patch.object(library_search.config,'load',return_value=cfg), patch.object(engines,'run') as run:
            with self.assertRaises(ValueError):library_search.answer(Mock(),index,{'query':'question','model':'deleted-entry'})
            run.assert_not_called()

    def test_library_answer_uses_entry_policy_but_keywords_remain_lean(self):
        from easyread.chat import library_search
        from unittest.mock import Mock
        cfg=self.config(); library=Mock(root=Path('/library')); index=Mock()
        index.snapshot.return_value=([{'text':'paper'}],[])
        for mid, expanded in [('translate-a',False),('answer-b',True)]:
            calls=[]
            with patch.object(library_search.config,'load',return_value=cfg), \
                    patch.object(library_search,'rank',return_value=[{'title':'paper','text':'evidence'}]), \
                    patch.object(engines,'run',side_effect=lambda options,*a,**k:calls.append(options) or 'terms or answer'), \
                    patch.object(chat,'stream',return_value=iter(['expanded answer'])) as answer:
                library_search.answer(library,index,{'query':'explain','model':mid})
            self.assertEqual(answer.called,expanded)
            self.assertTrue(all(c['codex']['lean'] and '_qa_policy' not in c['codex'] and '_qa_model_id' not in c for c in calls))


class CallIsolationTest(unittest.TestCase):
    def test_library_keywords_stay_lean_while_final_answer_opts_into_qa(self):
        from easyread.chat import library_search
        from unittest.mock import Mock
        cfg={'engine':'codex','codex':{'model':'m'},'qa_cli':{'codex':{'mode':'full'}}}
        library=Mock(root=Path('/library'))
        index=Mock();index.snapshot.return_value=([{'text':'paper'}],[])
        calls=[]
        def keywords(value,*args,**kwargs):calls.append(value);return 'retrieval terms'
        with patch.object(library_search.config,'load',return_value=cfg), patch.object(library_search.chat_models,'engine_cfg',return_value=(copy.deepcopy(cfg),{'name':'m'})), patch.object(library_search,'rank',return_value=[{'title':'paper','text':'evidence'}]), patch.object(engines,'run',side_effect=keywords), patch.object(chat,'stream',return_value=iter(['answer'])) as answer:
            result=library_search.answer(library,index,{'query':'explain'})
        self.assertEqual(result['answer'],'answer')
        self.assertEqual(len(calls),1);self.assertTrue(calls[0]['codex']['lean'])
        self.assertNotIn('_qa_policy',calls[0]['codex'])
        self.assertTrue(answer.call_args.kwargs['qa'])
        self.assertEqual(answer.call_args.args[2],library.root)

    def test_claude_only_user_qa_selects_expanded_policy(self):
        cfg={'engine':'claude','claude':{'model':'m'},'qa_cli':{'claude':{'mode':'full'}}}
        calls=[]
        def stream(args,*rest):calls.append(args);yield 'ok'
        with patch.object(chat.netcheck,'problem',return_value=None), patch.object(engines,'claude_path',return_value='claude'), patch.object(chat,'_stream_once',side_effect=stream), patch.object(qa,'_skills',return_value=[]):
            list(chat.stream(cfg,'question',Path('.'),threading.Event()))
            list(chat.stream(cfg,'question',Path('.'),threading.Event(),qa=True))
        self.assertIn('--disable-slash-commands',calls[0])
        self.assertIn('--strict-mcp-config',calls[0])
        for flag in ('--disable-slash-commands','--strict-mcp-config','--tools','--disallowedTools'):
            self.assertNotIn(flag,calls[1])
        self.assertNotIn('_qa_policy',cfg['claude'])

    def test_codex_translation_default_and_full_arguments_are_isolated(self):
        calls=[]
        def communicate(args,*rest):
            calls.append(args);Path(args[args.index('-o')+1]).write_text('ok');return ''
        with patch.object(engines,'_popen',side_effect=lambda args,cwd:args), patch.object(engines,'_communicate',side_effect=communicate):
            engines._codex_once('codex',{},'q',Path('.'),[],None,engines.codex_lean.features())
            engines._codex_once('codex',{'_chat':True},'q',Path('.'),[],None,engines.codex_lean.features())
            engines._codex_once('codex',{'_qa_policy':qa.validate('codex',{'mode':'full'}),'_chat':True},'q',Path('.'),[],None,[])
        self.assertIn('--sandbox',calls[0]);self.assertIn('--sandbox',calls[1]);self.assertNotIn('--sandbox',calls[2])
        self.assertIn('features.skill_search=false',calls[0]);self.assertIn('features.skill_search=false',calls[1])
        self.assertNotIn('features.skill_search=false',calls[2]);self.assertNotIn('web_search="live"',calls[2])

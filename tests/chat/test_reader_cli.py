"""Reader CLI modes and actual fixture-file reads; no provider calls."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from easyread.chat import chat, chat_models
from easyread.engines import engines, qa_capabilities as qa
from tests.reader_cli_fixture import prepare, SENTINEL


class ReaderCLITest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)
        env.start(); self.addCleanup(env.stop)
        tmp = tempfile.TemporaryDirectory(prefix="easyread-reader-test-")
        self.addCleanup(tmp.cleanup)
        self.ws, self.cfg = prepare(tmp.name)
        popen = engines._popen
        def tracked(*args, **kwargs):
            proc = popen(*args, **kwargs)
            def reap():
                proc.wait(timeout=5)
                for stream in (proc.stdin,proc.stdout,proc.stderr):
                    if stream: stream.close()
            self.addCleanup(reap)
            return proc
        tracked_popen = patch.object(engines,"_popen",side_effect=tracked)
        tracked_popen.start(); self.addCleanup(tracked_popen.stop)
        for target, value in [("easyread.engines.netcheck.problem", None),
                              ("easyread.engines.qa_capabilities._skills", []),
                              ("easyread.app.native_cli_settings.codex_effective", {})]:
            mock = patch(target, return_value=value)
            mock.start(); self.addCleanup(mock.stop)

    def answer(self, engine, policy):
        cfg = copy.deepcopy(self.cfg)
        cfg["qa_model_policies"] = {engine + "-test": {"engine":engine,"settings":policy}}
        ecfg, _ = chat_models.engine_cfg(cfg, engine + "-test")
        prompt = chat.prompt(self.ws, [{"role":"user","content":"Explain the distant result"}], "p1", "Local paragraph 1.", engine,
                             context_scope="selection")
        self.assertNotIn(SENTINEL, prompt)
        output = "".join(chat.stream(ecfg, prompt, self.ws.root, threading.Event(), qa=True))
        calls = [json.loads(line) for line in Path(os.environ["READER_CLI_LOG"]).read_text().splitlines()]
        return output, calls[-1], ecfg

    def test_reader_engine_mode_matrix_reads_actual_paper_in_correct_cwd(self):
        for engine, tools in (("claude",["Read"]),("codex",["shell"]),("pi",["read"])):
            for mode in ("default","full","custom"):
                with self.subTest(engine=engine,mode=mode):
                    policy = {"mode":mode,"allowed_tools":tools,"web_search":False,"web_fetch":False}
                    answer, call, cfg = self.answer(engine, policy)
                    self.assertIn(SENTINEL, answer)
                    self.assertEqual(call["cwd"],str(self.ws.root))
                    self.assertFalse(call["inline"])
                    args = call["argv"]
                    if engine == "codex":
                        self.assertIn("--dangerously-bypass-approvals-and-sandbox",args)
                        self.assertNotIn("--sandbox",args)
                    elif engine == "claude":
                        self.assertEqual(args[args.index("--tools")+1]=="default",mode=="full")
                        self.assertEqual("bypassPermissions" in args,mode=="full")
                    else:
                        self.assertEqual("--no-extensions" in args,mode != "full")
                        self.assertEqual("--approve" in args,mode=="full")
                    self.assertNotIn("_qa_policy",cfg[engine],"policy preparation must not mutate the selected model")

    def test_empty_custom_tools_and_legacy_sandbox_never_disable_paper_access(self):
        for engine in qa.ENGINES:
            answer, call, cfg = self.answer(engine,{"mode":"custom","allowed_tools":[],"sandbox":"read-only","web_search":False,"web_fetch":False})
            self.assertIn(SENTINEL,answer)
            self.assertFalse(call["blocked"])
            translated = engines.for_translation(qa.prepare(cfg))
            self.assertNotIn("_qa_policy",translated[engine])
            self.assertNotIn("_chat",translated[engine])
        self.assertEqual(len(Path(os.environ["READER_CLI_LOG"]).read_text().splitlines()),3)

    def test_all_modes_include_the_required_reading_tools_and_guidance(self):
        for engine in qa.ENGINES:
            selected = qa.validate(engine,{"mode":"custom","web_search":False,"web_fetch":False})
            prompt = chat.prompt(self.ws,[{"role":"user","content":"Explain"}],"p1","",engine,context_scope="selection")
            self.assertIn("论文读取工具已默认提供",prompt)
            self.assertIn("读取当前目录",prompt)
            self.assertTrue(set(qa.REQUIRED_TOOLS[engine]).issubset(selected["allowed_tools"]))
        from easyread.engines import chat_tools
        self.assertIn("read-only inspection commands",chat_tools.research_prompt("codex"))
        self.assertIn("Do not modify files or run commands",chat_tools.research_prompt("claude"))

    def test_claude_full_honors_extra_args_but_restricted_modes_do_not(self):
        self.cfg["claude"]["extra_args"] = ["--add-dir",str(self.ws.root)]
        for mode in ("full","default","custom"):
            _, call, _ = self.answer("claude",{"mode":mode,"allowed_tools":["Read"]})
            self.assertEqual("--add-dir" in call["argv"],mode=="full")
        self.cfg["claude"]["extra_args"] = ["--resume","wrong-session"]
        with self.assertRaisesRegex(engines.EngineError,"协议|会话"):
            self.answer("claude",{"mode":"full"})

"""Pi one-shot arguments and native JSON protocol; no model service is called."""
import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from easyread.engines import engines, pi_cli, qa_capabilities as qa, usage


def message(text="answer", reason="stop", **extra):
    return {"type": "message_end", "message": {
        "role": "assistant", "provider": "gateway", "model": "vendor/model",
        "content": [{"type": "text", "text": text}], "stopReason": reason,
        "usage": {"input": 10, "output": 7, "reasoning": 3, "cacheRead": 5, "cacheWrite": 2,
                  "cost": {"total": .125}}, **extra}}


def wire(*events):
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"


class PiArgumentsTest(unittest.TestCase):
    def test_lean_and_translation_flags_are_enforced(self):
        cfg = {"engine": "pi", "pi": {"model": "gateway/vendor/model", "reasoning_effort": "off"},
               "qa_cli": {"pi": {"mode": "full"}}}
        translated = engines.for_translation(qa.prepare(cfg))
        args = pi_cli.build_args("pi", translated["pi"], [Path("picture.png")])
        for flag in ("--no-approve", "--offline", "--no-extensions", "--no-mcp", "--no-skills",
                     "--no-context-files", "--no-prompt-templates", "--no-themes", "--no-session"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--tools") + 1], "read")
        self.assertEqual(args[args.index("--provider") + 1], "gateway")
        self.assertEqual(args[args.index("--model") + 1], "vendor/model")
        self.assertEqual(args[args.index("--thinking") + 1], "off")
        self.assertIn("--system-prompt", args)
        self.assertIn("--append-system-prompt", args)
        self.assertTrue(args[-1].startswith("@"))
        self.assertNotIn("--approve", args)
        self.assertNotIn("--sandbox", args)
        self.assertEqual(engines.image_mode(cfg), "attached")

    def test_full_preserves_native_packages_tools_and_trust(self):
        args = pi_cli.build_args("pi", {"_qa_policy": {"mode": "full"}})
        self.assertEqual(args, ["pi", "--mode", "json", "--print", "--no-session", "--approve", "--"])

    def test_custom_readonly_selection_and_unsupported_controls(self):
        selected = qa.validate("pi", {"mode": "custom", "allowed_tools": ["read", "grep", "ls", "find"]})
        self.assertFalse(selected["web_search"])
        args = pi_cli.build_args("pi", {"_qa_policy": selected})
        self.assertEqual(set(args[args.index("--tools") + 1].split(",")), set(qa.PI_TOOLS))
        empty = pi_cli.build_args("pi", {"_qa_policy": {"mode": "custom"}})
        self.assertNotIn("--no-tools", empty)
        self.assertIn("read", empty[empty.index("--tools") + 1].split(","))
        for change in ({"mcp": True}, {"web_search": True}, {"web_fetch": True},
                       {"sandbox": "danger-full-access"}, {"allowed_tools": ["bash"]},
                       {"allowed_tools": ["mcp__*"]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                qa.validate("pi", {"mode": "custom", **change})
        self.assertEqual(qa.validate("pi", {"mode": "full"})["mode"], "full")
        self.assertEqual(qa.DEFAULT["web_search"], True)

    def test_unsafe_extra_args_and_unqualified_models_fail_closed(self):
        for extra in (["--extension", "unsafe.js"], ["--approve"], ["--tools", "bash"], ["--skill", "unsafe"], ["--"]):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                pi_cli.build_args("pi", {"extra_args": extra})
        for value in ("bare-model", "/model", "provider/"):
            with self.assertRaises(ValueError):
                pi_cli.build_args("pi", {"model": value})
        with self.assertRaises(ValueError):
            pi_cli.build_args("pi", {"reasoning_effort": "ultra"})
        with self.assertRaises(ValueError):
            pi_cli.build_args("pi", {"_qa_policy": {"mode": "full"}, "extra_args": ["--resume"]})

    def test_skill_selection_is_qa_only_and_full_does_not_filter_packages(self):
        items = [dict(id="native:a", name="a", path="/native/a/SKILL.md", managed=False),
                 dict(id="managed:b", name="b", path="/qa/b/SKILL.md", managed=True)]
        with patch.object(qa, "_skills", return_value=items), patch("easyread.app.pi_skills.enabled", return_value=True):
            for raw, expected in [({"mode": "default"}, []),
                                  ({"mode": "full"}, ["--skill", items[1]["path"]]),
                                  ({"mode": "custom", "skills": True}, ["--skill", items[0]["path"], "--skill", items[1]["path"]]),
                                  ({"mode": "custom", "skills": True, "skill_overrides": {"native:a": False}}, ["--skill", items[1]["path"]])]:
                with qa.invocation("pi", qa.validate("pi", raw), "question") as (args, prompt):
                    self.assertEqual(args, expected)
                    self.assertEqual(prompt, "question")
        with self.assertRaisesRegex(ValueError, "native skill overrides"):
            qa.validate("pi", {"mode": "full", "skill_overrides": {"native:a": False}})


class PiProtocolTest(unittest.TestCase):
    def test_completed_messages_usage_unicode_and_identity(self):
        first = message("tool preamble", "toolUse")
        final = message("text\u2028with\u2029separators")
        parsed = pi_cli.parse(wire({"type": "message_update", "usage": {"input": 900},
                                   "assistantMessageEvent": {"type": "text_delta", "delta": "duplicate"}},
                                  first, {"type": "turn_end", "message": first["message"]},
                                  final, {"type": "agent_end", "messages": [final["message"]]},
                                  {"type": "agent_settled", "aborted": False}))
        self.assertEqual(parsed["text"], "text\u2028with\u2029separators")
        self.assertEqual(parsed["model"], "gateway/vendor/model")
        self.assertEqual(parsed["usage"], [{"input": 17, "cached": 5, "output": 7, "cost_usd": .125}] * 2)
        self.assertTrue(parsed["settled"])

    def test_compaction_and_nested_tool_usage_count_once(self):
        used = {"input": 4, "output": 2, "cacheRead": 1, "cacheWrite": 0}
        parsed = pi_cli.parse(wire({"type": "compaction_end", "result": {"usage": used}},
                                  {"type": "message_end", "message": {"role": "toolResult", "usage": used}},
                                  {"type": "tool_execution_end", "result": {"usage": used}}))
        self.assertEqual(parsed["usage"], [{"input": 5, "cached": 1, "output": 2}] * 2)

    def run_events(self, output, returncode=0):
        proc = Mock(returncode=returncode)
        proc.poll.return_value = returncode
        report = Mock()
        meter = usage.Meter("pi")
        with patch.object(pi_cli, "path", return_value="pi"), patch.object(engines, "_popen", return_value=proc), \
                patch.object(pi_cli, "_communicate", return_value=output):
            text = pi_cli.run({"model": "gateway/vendor/model", "_report_model": report}, "q", Path("."), meter=meter)
        return text, meter.snapshot(), report

    def test_success_and_retry_recovery_use_last_authoritative_answer(self):
        text, meter, report = self.run_events(wire(message("", "error", errorMessage="retry me"),
                                                 {"type": "auto_retry_start"}, message(),
                                                 {"type": "agent_settled", "aborted": False}))
        self.assertEqual(text, "answer")
        self.assertEqual(meter["input"], 34)
        report.assert_called_once_with("gateway/vendor/model")

    def test_terminal_failure_cancel_limit_and_incomplete_are_not_answers(self):
        for reason, exception in [("error", engines.EngineError), ("aborted", engines.Cancelled),
                                  ("length", engines.OutputLimitReached), ("toolUse", engines.EngineError)]:
            with self.subTest(reason=reason), self.assertRaises(exception):
                self.run_events(wire(message("partial", reason), {"type": "agent_settled", "aborted": reason == "aborted"}))
        with self.assertRaises(engines.EngineError):
            self.run_events(wire(message()))
        with self.assertRaises(engines.EngineError):
            self.run_events(wire(message(), {"type": "agent_settled"}), returncode=1)

    def test_safety_flag_failure_never_retries(self):
        proc = Mock(returncode=1)
        proc.poll.return_value = 1
        with patch.object(pi_cli, "path", return_value="pi"), patch.object(engines, "_popen", return_value=proc) as popen, \
                patch.object(pi_cli, "_communicate", side_effect=engines.EngineError("unknown --no-extensions")):
            with self.assertRaises(engines.EngineError):
                pi_cli.run({}, "q", Path("."))
        self.assertEqual(popen.call_count, 1)
        proc.wait.assert_called_once()

    def test_real_subprocess_cancel_and_timeout_are_reaped(self):
        for cancel_run in (False, True):
            cancel = threading.Event()
            processes = []
            def spawn(*args):
                proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                processes.append(proc)
                return proc
            timer = threading.Timer(.1, cancel.set) if cancel_run else None
            if timer:
                timer.start()
            try:
                with patch.object(pi_cli, "path", return_value="pi"), patch.object(engines, "_popen", side_effect=spawn):
                    with self.assertRaises(engines.Cancelled if cancel_run else engines.EngineError):
                        pi_cli.run({"timeout": 1}, "q", Path("."), cancel=cancel)
                self.assertIsNotNone(processes[0].poll())
            finally:
                if timer:
                    timer.join()
                for proc in processes:
                    for stream in (proc.stdin, proc.stdout, proc.stderr):
                        if stream and not stream.closed:
                            stream.close()


if __name__ == "__main__":
    unittest.main()

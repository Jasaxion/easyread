"""Pi workspace JSON adapter, policy and native continuation; no provider calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import types
import unittest
from unittest.mock import patch

from easyread.workspaces import pi_sessions as pi, session_cli as cli, session_reasoning as reasoning
from easyread.workspaces import session_terminal as term, model_catalog
from easyread.workspaces.sessions import SessionError
from tests import test_workspace_sessions as fixtures


class PiWorkspaceTest(unittest.TestCase):
    setUp = fixtures.WorkspaceSessionsTest.setUp
    native = fixtures.WorkspaceSessionsTest.native
    wait_idle = fixtures.WorkspaceSessionsTest.wait_idle

    def create(self, **kwargs):
        with patch.object(model_catalog, "listing", return_value={"models": [], "default": ""}):
            return self.sessions.create(self.pid, {"engine": "pi", **kwargs})["session"]

    def args(self, record, resume=False, interactive=False, effort=""):
        home = cli.project_home(self.store.meta(self.pid), "pi")
        kwargs = dict(native_id=record["native_id"], resume=resume, model="provider/model/id",
                      cli_home=home, root=self.store.root(self.pid), reasoning_effort=effort,
                      libroot=self.store.libroot, pid=self.pid)
        with patch.object(cli, "executable", return_value="fake-pi"):
            return (cli.terminal_argv("pi", **kwargs) if interactive else
                    cli.argv("pi", exe="fake-pi", prompt_stdin=True, **kwargs))

    def test_store_and_idle_controls_preserve_identity_and_validate_policy_model_effort(self):
        self.assertEqual(self.store.allocate({"name": "Pi project", "engine": "pi"})["engine"], "pi")
        record = self.create(model="openai/gpt-test", reasoning_effort="off")
        self.assertEqual(record["adapter"], "pi_json")
        self.assertNotIn("capability_mode", record)
        with patch.object(model_catalog, "listing", return_value={"models": [], "default": ""}):
            updated = self.sessions.settings(self.pid, record["id"], {
                "model": "anthropic/claude-test", "reasoning_effort": "max"})["session"]
        self.assertEqual(updated["native_id"], record["native_id"])
        self.assertNotIn("capability_mode", updated)
        self.assertEqual(updated["reasoning_effort"], "max")
        for body in ({"capability_mode": "sandbox"}, {"capability_mode": None}, {"model": "sonnet"},
                     {"model": "openai/test:high"}, {"model": "openai/test\n"}, {"reasoning_effort": "none"},
                     {"reasoning_effort": "ultra"}):
            with self.subTest(body=body), self.assertRaises(SessionError):
                self.sessions.settings(self.pid, record["id"], body)
        self.sessions._commit(self.pid, lambda data: data["sessions"][record["id"]].update(state="running"))
        with self.assertRaises(SessionError):
            self.sessions.settings(self.pid, record["id"], {"capability_mode": "lean"})
        self.assertNotIn("capability_mode", self.sessions.get(self.pid, record["id"])["session"])

    def test_full_headless_terminal_args_and_exact_native_resume_path(self):
        record = self.create()
        _, path = self.native(record)
        for interactive in (False, True):
            for resume in (False, True):
                for effort in ("", *reasoning.LEVELS["pi"]):
                    args = self.args(record, resume, interactive, effort)
                    self.assertEqual("--mode" in args, not interactive)
                    self.assertEqual(args[args.index("--session-dir") + 1], str(path.parent))
                    self.assertEqual(args[args.index("--session" if resume else "--session-id") + 1],
                                     str(path) if resume else record["native_id"])
                    self.assertEqual(args[args.index("--provider") + 1], "provider")
                    self.assertEqual(args[args.index("--model") + 1], "model/id")
                    self.assertEqual("--thinking" in args, bool(effort))
                    if effort:
                        self.assertEqual(args[args.index("--thinking") + 1], effort)
                    for arg in ("--no-extensions", "--no-mcp", "--no-skills", "--tools", "--no-approve",
                                "--approve", "--permission-mode", "--effort", "--resume", "-p", "-c",
                                "--dangerously-bypass-approvals-and-sandbox", "--mcp-config", "-"):
                        self.assertNotIn(arg, args)
        path.unlink()
        with self.assertRaises(SessionError):
            self.args(record, resume=True)

    def test_terminal_uses_native_full_even_for_pre_release_lean_metadata(self):
        record = self.create()
        self.sessions._commit(self.pid, lambda data: data["sessions"][record["id"]].update(capability_mode="lean"))
        with patch.object(term, "unavailable_reason", return_value=""), patch.object(cli, "executable", return_value="pi"):
            self.sessions.terminal(self.pid, record["id"])
        args = self.sessions.terminals.calls[-1]["argv"]
        for option in ("--no-extensions", "--no-skills", "--no-mcp", "--tools", "--approve"):
            self.assertNotIn(option, args)
        self.assertNotIn("capability_mode", self.sessions.get(self.pid, record["id"])["session"])
        self.sessions.terminals.close_scope(self.pid, record["id"])

    def test_capabilities_do_not_advertise_codex_controls_or_project_mcp(self):
        with patch("easyread.workspaces.codex_protocol.probe", return_value={"methods": []}):
            capabilities = self.sessions.capabilities()["engines"]["pi"]
        self.assertNotIn("capability_modes", capabilities)
        self.assertTrue(capabilities["native_configuration"])
        self.assertFalse(any(capabilities[key] for key in ("goal", "steer", "user_input", "sandbox", "project_mcp")))
        record = self.create()
        with self.assertRaises(SessionError):
            self.sessions.goal(self.pid, record["id"], {"action": "set", "objective": "not supported"})

    def test_catalog_delegates_safe_core_metadata_and_filters_nonqualified_or_private_values(self):
        from easyread import app
        seen = []
        def catalog(cfg):
            seen.append(cfg)
            return {"default": "openai/default", "configured_reasoning": "off", "models": [
                {"id": "openai/test", "name": "Test", "reasoning_levels": ["off", "high", "ultra"]},
                {"id": "anthropic/second", "name": "https://private.invalid/key"},
                {"id": "unqualified"}, {"id": "openai/test:high"},
                {"id": "Bearer secret-value"}], "diagnostics": ["secret diagnostics must not be forwarded"]}
        helper = types.SimpleNamespace(catalog=catalog)
        with patch.object(app, "pi_models", helper, create=True), patch.object(cli, "executable", return_value="pi"), \
                patch.object(cli, "home", side_effect=AssertionError("No native preparation")), \
                patch.object(subprocess, "Popen", side_effect=AssertionError("No process")):
            result = model_catalog.listing(self.store, self.pid, "pi", cfg={"pi": {"model": "openai/test"}}, refresh=True)
        self.assertEqual(seen, [{"model": "openai/test"}])
        self.assertEqual([row["id"] for row in result["models"]], ["openai/default", "openai/test", "anthropic/second"])
        self.assertEqual(result["configured_reasoning"], "off")
        self.assertEqual(result["models"][1]["reasoning_levels"], ["off", "high"])
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["warning"], "availability_unverified")
        self.assertFalse((self.store.meta(self.pid) / "sessions").exists())

    def test_catalog_preserves_scoped_and_router_model_ids(self):
        from easyread.app import pi_models
        ids = ["cloudflare-workers-ai/@cf/meta/llama", "openrouter/~provider/model"]
        for identity in ids:
            self.assertEqual(pi.model(identity), identity)
        with patch.object(pi_models, "catalog", return_value={"models": [{"id": value} for value in ids], "default": ids[0]}):
            result = model_catalog._pi("pi", {"pi": {}})
        self.assertEqual([row["id"] for row in result["models"]], ids)
        self.assertEqual(result["default"], ids[0])

    def test_clipboard_image_becomes_native_attachment_not_only_prompt_reference(self):
        from tests.test_qa_images import picture
        script = self.base / "fake_pi.py"
        script.write_text(FAKE_PI, encoding="utf-8")
        record = self.create(model="provider/model")
        popen, calls = subprocess.Popen, []
        def start(args, **kwargs):
            calls.append(args)
            return popen([sys.executable, str(script), *args[1:]], **kwargs)
        with patch.object(cli, "executable", return_value="fake-pi"), patch("easyread.workspaces.sessions.subprocess.Popen", side_effect=start):
            self.sessions.send(self.pid, record["id"], {"images": [picture()]})
            self.wait_idle()
        result = self.sessions.get(self.pid, record["id"])["session"]
        self.assertEqual(result["state"], "idle", result["error"])
        image = self.store.root(self.pid) / result["messages"][0]["images"][0]
        self.assertEqual(calls[0][-2:], ["--", "@" + str(image)])
        self.assertTrue(image.is_file())
        self.assertFalse(any("base64" in arg for arg in calls[0]))

    def test_native_config_trust_and_credentials_are_never_copied_or_modified(self):
        host = cli.host_home("pi")
        host.mkdir()
        for name in ("auth.json", "settings.json", "trust.json", "mcp.json", "models.json"):
            (host / name).write_text('"credential-sentinel"')
        before = {p.name: p.read_bytes() for p in host.iterdir()}
        with patch.dict(os.environ, {"EASYREAD_WORKSPACE_CLI_ISOLATION": "0", "PI_CODING_AGENT_SESSION_DIR": "ignore"}):
            record = self.create()
            env, home, isolated = cli.launch_env(self.store.meta(self.pid), "pi", False)
        self.assertTrue(record["cli_isolated"])
        self.assertTrue(isolated)
        self.assertEqual(env["PI_CODING_AGENT_DIR"], str(host))
        self.assertEqual(home, cli.project_home(self.store.meta(self.pid), "pi"))
        self.assertFalse(any((home / name).exists() for name in before))
        self.assertEqual(before, {p.name: p.read_bytes() for p in host.iterdir()})

    def test_stream_authoritative_text_unicode_tools_usage_model_and_no_thinking(self):
        stream = cli.Stream("pi")
        def feed(event):
            return stream.feed(json.dumps(event, ensure_ascii=False) + "\n")
        feed({"type": "session", "id": cli.new_native_id("pi")})
        feed({"type": "message_start", "message": {"role": "assistant"}})
        self.assertEqual(feed({"type": "message_update", "assistantMessageEvent": {
            "type": "thinking_delta", "contentIndex": 0, "delta": "SECRET-THINKING"}}), [])
        feed({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 1, "delta": "old"}})
        feed({"type": "message_update", "assistantMessageEvent": {"type": "text_end", "contentIndex": 1, "content": "new\u2028line"}})
        self.assertEqual(stream.text, "new\u2028line")
        events = feed({"type": "message_end", "message": {"role": "assistant", "provider": "openai", "model": "test",
            "content": [{"type": "text", "text": "final"}, {"type": "thinking", "thinking": "SECRET-THINKING"}],
            "stopReason": "stop", "usage": {"input": 3, "output": 4, "cacheRead": 5, "cacheWrite": 6, "cost": {"total": .2}}}})
        self.assertEqual(events[0]["text"], "final")
        self.assertEqual(stream.model, "openai/test")
        feed({"type": "thinking_level_changed", "level": "minimal"})
        self.assertEqual(stream.reasoning_effort, "minimal")
        self.assertEqual(stream.usage, {"input": 14, "output": 4, "cached": 5, "cost_usd": .2})
        events = feed({"type": "tool_execution_start", "toolCallId": "call", "toolName": "read", "args": {"path": "file"}})
        self.assertEqual((events[0]["kind"], events[0]["id"]), ("tool", "call"))
        events = feed({"type": "tool_execution_end", "toolCallId": "call", "toolName": "read", "isError": True,
                       "result": {"content": [{"type": "text", "text": "missing"}]}})
        self.assertEqual(events[0]["text"], "missing")
        self.assertFalse(events[0]["ok"])
        feed({"type": "agent_end", "willRetry": False})
        self.assertFalse(stream.finished)
        feed({"type": "message_start", "message": {"role": "assistant"}})
        feed({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "answer"}],
                                                  "usage": {"input": 2, "output": 1}}})
        feed({"type": "agent_settled", "aborted": False})
        self.assertTrue(stream.finished)
        self.assertEqual(stream.text, "final\n\nanswer")
        self.assertEqual(stream.usage["input"], 16)
        self.assertNotIn("SECRET-THINKING", json.dumps(events))

    def test_streaming_projection_flushes_without_quadratic_text_events(self):
        record = self.create()
        snapshots = []
        sessions, pid, sid = self.sessions, self.pid, record["id"]
        class PiProcess(fixtures.ControlledProcess):
            def lines(self, native_id):
                yield json.dumps({"type": "session", "id": native_id})
                yield '{"type":"message_start","message":{"role":"assistant"}}'
                for i in range(300):
                    yield '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","contentIndex":0,"delta":"a"}}'
                    if i == 99:
                        snapshots.append(sessions.get(pid, sid)["session"])
                yield json.dumps({"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "content": [{"type": "text", "text": "a" * 300}]}})
                yield '{"type":"agent_settled","aborted":false}'
                self.returncode = 0
        proc = PiProcess(record["native_id"])
        with patch.object(cli, "executable", return_value="fake-pi"), patch("easyread.workspaces.sessions.subprocess.Popen", return_value=proc), \
                patch("easyread.workspaces.sessions.FLUSH_INTERVAL", 0):
            self.sessions.send(pid, sid, {"message": "stream"})
            self.wait_idle()
        self.assertEqual(snapshots[0]["messages"][-1]["content"], "a" * 100)
        self.assertTrue(snapshots[0]["messages"][-1]["partial"])
        result = self.sessions.get(pid, sid)["session"]
        self.assertEqual(result["messages"][-1]["content"], "a" * 300)
        self.assertNotIn("partial", result["messages"][-1])
        self.assertLess(len(result["events"]), 10)
        self.assertEqual(len([event for event in result["events"] if event["kind"] == "text"]), 1)

    def test_incomplete_or_missing_assistant_response_is_not_success(self):
        for reason in ("length", "toolUse", ""):
            stream = cli.Stream("pi")
            if reason:
                stream.feed(json.dumps({"type": "message_end", "message": {"role": "assistant", "stopReason": reason,
                    "content": [{"type": "text", "text": "partial answer"}]}}))
            result = stream.feed('{"type":"agent_settled","aborted":false}')
            self.assertFalse(result[0]["ok"])
            self.assertTrue(stream.error)
            if reason:
                self.assertIn("partial answer", stream.text)

    def test_stream_provider_errors_retries_and_aborts_are_not_successes(self):
        for event in ({"type": "message_end", "message": {"role": "assistant", "stopReason": "error", "errorMessage": "denied"}},
                      {"type": "auto_retry_end", "success": False, "finalError": "exhausted"}):
            stream = cli.Stream("pi")
            stream.feed(json.dumps(event))
            self.assertTrue(stream.error)
            result = stream.feed('{"type":"agent_settled","aborted":false}')
            self.assertFalse(result[0]["ok"])
        stream.feed('{"type":"message_start","message":{"role":"assistant"}}')
        stream.feed('{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"recovered"}]}}')
        self.assertEqual(stream.error, "")
        self.assertFalse(stream.feed('{"type":"agent_settled","aborted":true}')[0]["ok"])

    def test_native_transcript_validation_ownership_links_and_ui_text_only(self):
        record = self.create()
        native_id, path = self.native(record)
        raw = path.read_text()
        rows = [json.loads(line) for line in raw.split("\n") if line]
        rows[-1]["message"]["content"].append({"type": "thinking", "thinking": "private"})
        rows[-1]["message"]["content"][0]["text"] = "answer\u2028separator"
        raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
        cli.write_transcript(path, raw)
        self.assertEqual(cli.transcript("pi", path.parent.parent, native_id, self.store.root(self.pid)), path)
        self.assertIsNone(cli.transcript("pi", path.parent.parent, native_id, self.base))
        self.assertEqual(cli.transcript_messages("pi", raw)[-1]["content"], "answer\u2028separator")
        for broken in (raw + "{", raw.replace('"version": 3', '"version": 4'), raw.replace('"parentId": null', '"parentId": "missing"')):
            with self.assertRaises(SessionError):
                cli.validate_transcript("pi", broken)
        self.assertEqual(len(cli.project_transcripts("pi", path.parent.parent, self.store.root(self.pid))), 1)
        duplicate = path.with_name("duplicate_" + native_id + ".jsonl")
        duplicate.write_text(raw)
        with self.assertRaises(SessionError):
            cli.transcript("pi", path.parent.parent, native_id, self.store.root(self.pid))
        duplicate.unlink()
        original = self.base / "original.jsonl"
        original.write_text(raw)
        path.unlink()
        path.symlink_to(original)
        with self.assertRaises(ValueError):
            cli.transcript("pi", path.parent.parent, native_id, self.store.root(self.pid))

    def test_archive_roundtrip_preserves_native_tree_and_excludes_credentials(self):
        record = self.create()
        native_id, path = self.native(record, turns=2)
        self.sessions._commit(self.pid, lambda data: data["sessions"][record["id"]].update(native_started=True))
        home = path.parent.parent
        (home / "auth.json").write_text("CREDENTIAL-SENTINEL")
        destination = self.base / "export-pi"
        with patch.object(cli, "launch_env", side_effect=AssertionError("No launch/config preparation")):
            self.sessions.export_state(self.pid, destination)
            new_pid = self.store.allocate({"name": "Imported Pi", "engine": "pi"})["id"]
            result = self.sessions.import_state(new_pid, destination)
        self.assertEqual(result["native"], 1)
        for file in destination.rglob("*"):
            if file.is_file():
                self.assertNotIn(b"CREDENTIAL-SENTINEL", file.read_bytes())
        imported = self.sessions.get(new_pid, record["id"])["session"]
        self.assertEqual(imported["native_id"], native_id)
        self.assertNotIn("capability_mode", imported)
        target = cli.transcript("pi", self.sessions._native_home(new_pid, imported), native_id, self.store.root(new_pid))
        rows = cli.validate_transcript("pi", target.read_text(), native_id)
        self.assertEqual(rows[0]["cwd"], str(self.store.root(new_pid)))
        self.assertEqual(len(cli.transcript_messages("pi", target.read_text())), 4)

    def test_fake_subprocess_two_turns_really_resume_native_file_and_persist_usage(self):
        script = self.base / "fake_pi.py"
        script.write_text(FAKE_PI, encoding="utf-8")
        record = self.create(model="provider/model")
        popen = subprocess.Popen
        calls = []
        def start(args, **kwargs):
            calls.append(args)
            return popen([sys.executable, str(script), *args[1:]], **kwargs)
        with patch.object(cli, "executable", return_value="fake-pi"), patch("easyread.workspaces.sessions.subprocess.Popen", side_effect=start):
            for question in ("first\u2028question", "second"):
                self.sessions.send(self.pid, record["id"], {"message": question})
                self.wait_idle()
                result = self.sessions.get(self.pid, record["id"])["session"]
                self.assertEqual(result["state"], "idle", result["error"])
            self.assertIn("--session-id", calls[0])
            self.assertIn("--session", calls[1])
            path = Path(calls[1][calls[1].index("--session") + 1])
            messages = cli.transcript_messages("pi", path.read_text())
            self.assertEqual([m["content"] for m in messages], ["first\u2028question", "answer 1", "second", "answer 2"])
            self.assertEqual(result["reported_model"], "provider/model")
            self.assertEqual(result["usage"]["input"], 3)
            self.assertNotIn("secret-thinking", json.dumps(result))
            path.unlink()
            self.sessions.send(self.pid, record["id"], {"message": "must not restart"})
            self.wait_idle()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.sessions.get(self.pid, record["id"])["session"]["state"], "error")

    @unittest.skipUnless(os.environ.get("EASYREAD_PI_SMOKE_COMMAND"), "Set EASYREAD_PI_SMOKE_COMMAND for isolated native --help validation")
    def test_installed_pi_accepts_native_full_and_resume_flags_without_inference(self):
        executable = os.environ["EASYREAD_PI_SMOKE_COMMAND"]
        root = self.store.root(self.pid)
        agent = self.base / "empty-agent"
        agent.mkdir()
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.base), "PI_CODING_AGENT_DIR": str(agent),
               "PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1", "TERM": "dumb"}
        record = self.create()
        _, path = self.native(record)
        original = path.read_bytes()
        for resume in (False, True):
            for interactive in (False, True):
                args = pi.argv(exe=executable, native_id=record["native_id"], resume=resume,
                               cli_home=path.parent.parent, cwd=root, selected_model="openai/gpt-4o",
                               reasoning_effort="max", interactive=interactive)
                done = subprocess.run(args + ["--help"], stdin=subprocess.DEVNULL, cwd=root, env=env,
                                      capture_output=True, text=True, timeout=20)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn("--thinking", done.stdout + done.stderr)
        self.assertEqual(path.read_bytes(), original)

    def test_cancel_pi_waits_for_process_and_persists_interrupted(self):
        record = self.create()
        class PiProcess(fixtures.ControlledProcess):
            def lines(self, native_id):
                yield json.dumps({"type": "session", "id": native_id}) + "\n"
                self.gate.wait(5)
        proc = PiProcess(record["native_id"], threading.Event())
        spawned = threading.Event()
        def start(*args, **kwargs):
            spawned.set()
            return proc
        with patch.object(cli, "executable", return_value="fake-pi"), patch("easyread.workspaces.sessions.subprocess.Popen", side_effect=start), \
                patch.object(cli, "stop_process", side_effect=lambda p: p.stop()):
            self.sessions.send(self.pid, record["id"], {"message": "wait"})
            self.assertTrue(spawned.wait(3))
            result = self.sessions.cancel(self.pid, record["id"])
        self.assertEqual(result["session"]["state"], "interrupted")
        self.assertFalse(self.sessions.busy(self.pid))


FAKE_PI = r'''import json, os, pathlib, sys
args = sys.argv[1:]
def arg(key): return args[args.index(key) + 1]
assert arg("--mode") == "json"
assert not any(flag in args for flag in ("--no-extensions", "--no-mcp", "--tools", "--no-skills"))
assert "--approve" not in args and "--dangerously-bypass-approvals-and-sandbox" not in args
if "--session" in args:
    path = pathlib.Path(arg("--session"))
    rows = [json.loads(line) for line in path.read_text().split("\n") if line]
    assert rows[0]["cwd"] == os.getcwd()
else:
    native_id = arg("--session-id")
    path = pathlib.Path(arg("--session-dir")) / ("2026-01-01T00-00-00-000Z_" + native_id + ".jsonl")
    rows = [{"type": "session", "version": 3, "id": native_id, "cwd": os.getcwd()}]
question = sys.stdin.read()
turn = sum(row.get("message", {}).get("role") == "user" for row in rows) + 1
answer = "answer " + str(turn)
parent = rows[-1]["id"] if len(rows) > 1 else None
for role, text in (("user", question), ("assistant", answer)):
    rid = role + str(turn)
    rows.append({"type": "message", "id": rid, "parentId": parent, "message": {"role": role, "content": [{"type": "text", "text": text}]}})
    parent = rid
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
def emit(event): print(json.dumps(event, ensure_ascii=False), flush=True)
emit(rows[0])
emit({"type": "message_start", "message": {"role": "assistant"}})
emit({"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "secret-thinking"}})
emit({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": answer}})
emit({"type": "message_end", "message": {"role": "assistant", "provider": "provider", "model": "model", "stopReason": "stop", "content": [{"type": "text", "text": answer}], "usage": {"input": 3, "output": 2}}})
emit({"type": "agent_settled", "aborted": False})
'''


if __name__ == "__main__":
    unittest.main()

"""Native workspace sessions: argv, state ownership, lifecycle and MCP seams."""
import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from easyread.workspaces import session_cli as cli
from easyread.workspaces import session_terminal as term
from easyread.workspaces import mcp
from easyread.workspaces.sessions import Sessions, SessionError, KEEP_MESSAGES, KEEP_EVENTS
from easyread.workspaces.store import Store


def transcript(engine, native_id, cwd, turns=1):
    if engine == "codex":
        records = [{"type": "session_meta", "payload": {"id": native_id, "cwd": str(cwd), "source": "cli"}}]
        for i in range(turns):
            records += [{"type": "event_msg", "payload": {"type": "user_message", "message": f"question {i}"}},
                        {"type": "event_msg", "payload": {"type": "agent_message", "message": f"answer {i}"}}]
    else:
        records = [{"type": role, "sessionId": native_id, "cwd": str(cwd),
                    "uuid": str(uuid.uuid4()), "message": {"role": role, "content": [{"type": "text", "text": text}]}}
                   for i in range(turns) for role, text in (("user", f"question {i}"), ("assistant", f"answer {i}"))]
    return "".join(json.dumps(r) + "\n" for r in records)


class FakeTerminals:
    def __init__(self):
        self.active = {}
        self.calls = []
        self.access = None

    def scope_active(self, pid=None, sid=None):
        return any((pid is None or p == pid) and (sid is None or s == sid) for p, s in self.active)

    def scope_url(self, pid, sid, access_sid):
        return self.active.get((pid, sid))

    def open_native(self, **kwargs):
        self.calls.append(kwargs)
        self.active[kwargs["scope"]] = "/workspace-terminal/fake/"
        return {"url": "/workspace-terminal/fake/"}

    def close_scope(self, pid=None, sid=None):
        for p, s in list(self.active):
            if (pid is None or p == pid) and (sid is None or s == sid):
                self.active.pop((p, s))

    def close(self):
        self.active.clear()


class ControlledProcess:
    def __init__(self, native_id, gate=None):
        self.pid = 987654
        self.stdin = io.StringIO()
        self.stderr = io.StringIO()
        self.gate = gate or threading.Event()
        if gate is None:
            self.gate.set()
        self.returncode = None
        self.stdout = self.lines(native_id)

    def lines(self, native_id):
        yield json.dumps({"type": "system", "subtype": "init", "session_id": native_id})
        self.gate.wait(5)
        if self.returncode is None:
            yield json.dumps({"type": "result", "subtype": "success", "result": "controlled answer", "usage": {"input_tokens": 4}})
            self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self.gate.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def stop(self):
        self.returncode = -15
        self.gate.set()


class WorkspaceSessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="easyread-session-test-")
        self.base = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.base / "host-codex"),
                                          "CLAUDE_CONFIG_DIR": str(self.base / "host-claude"),
                                          "EASYREAD_WORKSPACE_CLI_ISOLATION": "1"})
        self.env.start()
        self.store = Store(self.base / "library")
        self.pid = self.store.allocate({"name": "Disposable research"})["id"]
        self.sessions = Sessions(self.store)
        self.sessions.terminals = FakeTerminals()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.sessions.close)

    def create(self, engine="codex"):
        return self.sessions.create(self.pid, {"engine": engine})["session"]

    def native(self, record, turns=1, cwd=None, home=None):
        engine = record["engine"]
        native_id = record.get("native_id") or cli.new_native_id(engine)
        cwd = cwd or self.store.root(self.pid)
        home = home or cli.project_home(self.store.meta(self.pid), engine)
        path = cli.transcript_target(engine, home, native_id, cwd)
        cli.write_transcript(path, transcript(engine, native_id, cwd, turns))
        return native_id, path

    def wait_idle(self, timeout=5):
        end = time.monotonic() + timeout
        while self.sessions.busy(self.pid) and time.monotonic() < end:
            time.sleep(.005)
        self.assertFalse(self.sessions.busy(self.pid), "background worker did not exit")

    def test_claude_workspace_timeout_is_explicit_unlimited_by_default(self):
        from easyread.workspaces.sessions import turn_timeout
        with patch.dict(os.environ, {'EASYREAD_WORKSPACE_TURN_TIMEOUT': ''}):
            self.assertEqual(turn_timeout(), 0)
        with patch.dict(os.environ, {'EASYREAD_WORKSPACE_TURN_TIMEOUT': str(3 * 24 * 3600)}):
            self.assertEqual(turn_timeout(), 3 * 24 * 3600)
        record = self.create('claude')
        proc = ControlledProcess(record['native_id'])
        with patch.dict(os.environ, {'EASYREAD_WORKSPACE_TURN_TIMEOUT': '0'}), \
                patch.object(cli, 'executable', return_value='fake'), \
                patch('easyread.workspaces.sessions.subprocess.Popen', return_value=proc), \
                patch('easyread.workspaces.sessions.threading.Timer', side_effect=AssertionError('No default timer')):
            self.sessions.send(self.pid, record['id'], {'message': 'ordinary long skill'})
            self.wait_idle()
        self.assertEqual(self.sessions.get(self.pid, record['id'])['session']['state'], 'idle')

    def test_uuid7_version_variant_and_timestamp(self):
        for _ in range(100):
            value = uuid.UUID(cli.uuid7())
            self.assertEqual(value.version, 7)
            self.assertEqual(value.variant, uuid.RFC_4122)
            self.assertLess(abs((value.int >> 80) - int(time.time() * 1000)), 1000)

    def test_argv_full_permissions_additive_mcp_and_native_tools(self):
        for engine in cli.ENGINES:
            for resume in (False, True):
                native_id = cli.new_native_id(engine)
                args = cli.argv(engine, exe=engine, prompt_stdin=True, native_id=native_id,
                                resume=resume, root=self.base, libroot=self.store.libroot, pid=self.pid)
                with patch.object(cli, "executable", return_value=engine):
                    tui = cli.terminal_argv(engine, native_id, resume=resume, libroot=self.store.libroot, pid=self.pid)
                for command in (args, tui):
                    self.assertIn(cli.MCP_NAME, " ".join(command))
                    self.assertNotIn("--strict-mcp-config", command)
                    self.assertNotIn("--tools", command)
                    self.assertNotIn("--disable-slash-commands", command)
                self.assertNotIn("--skip-git-repo-check", tui)
                if engine == "codex":
                    self.assertIn("--dangerously-bypass-approvals-and-sandbox", args)
                    if resume:
                        self.assertNotIn("--color", args)
                        self.assertNotIn("--cd", args)
                        self.assertNotIn("--sandbox", args)
                else:
                    self.assertIn("--resume" if resume else "--session-id", tui)

    def test_installed_cli_accepts_generated_help_arguments(self):
        checked = 0
        for engine in cli.ENGINES:
            exe = cli.executable(engine)
            if not exe:
                continue
            for resume in (False, True):
                kwargs = {"native_id": cli.new_native_id(engine), "resume": resume,
                          "libroot": self.store.libroot, "pid": self.pid}
                commands = [cli.argv(engine, exe=exe, prompt_stdin=True, root=self.base,
                                     last_message=self.base / "last", **kwargs),
                            cli.terminal_argv(engine, **kwargs)]
                for command in commands:
                    done = subprocess.run(command + ["--help"], capture_output=True, text=True, timeout=15)
                    self.assertEqual(done.returncode, 0, done.stderr[:500])
                    checked += 1
        if not checked:
            self.skipTest("Native CLIs are not installed")

    def test_recovery_is_durable(self):
        record = self.create()
        self.sessions._commit(self.pid, lambda data: data["sessions"][record["id"]].update(state="running"))
        restarted = Sessions(self.store)
        self.addCleanup(restarted.close)
        for _ in range(2):
            self.assertEqual(restarted.get(self.pid, record["id"])["session"]["state"], "interrupted")
        saved = restarted._load(self.pid)
        self.assertEqual(saved["sessions"][record["id"]]["state"], "interrupted")

    def test_malformed_state_never_erases_history(self):
        for raw in ("{broken", "[]", '{"version":1,"sessions":[],"order":[],"seq":0}'):
            self.sessions._file(self.pid).write_text(raw)
            with self.assertRaises(SessionError) as ctx:
                self.sessions.get(self.pid, "abcdef123456")
            self.assertEqual(ctx.exception.code, "invalid_state")
            self.assertEqual(self.sessions._file(self.pid).read_text(), raw)

    def test_resume_known_id_returns_existing_record(self):
        record = self.create("claude")
        result = self.sessions.create(self.pid, {"engine": "claude", "native_id": record["native_id"]})
        self.assertEqual(result["session"]["id"], record["id"])
        self.assertEqual(len(self.sessions._load(self.pid)["sessions"]), 1)

    def test_global_resume_requires_actual_project_cwd(self):
        with patch.dict(os.environ, {"EASYREAD_WORKSPACE_CLI_ISOLATION": "0"}):
            record = self.create()
            native_id, _ = self.native(record, cwd=self.base / "another-project", home=cli.host_home("codex"))
            with self.assertRaises(SessionError):
                self.sessions.create(self.pid, {"engine": "codex", "native_id": native_id})
            own_id, _ = self.native(record, home=cli.host_home("codex"))
            result = self.sessions.create(self.pid, {"engine": "codex", "native_id": own_id})
            self.assertEqual(result["session"]["messages"][0]["content"], "question 0")

    def test_transcript_symlink_and_hardlink_rejected(self):
        record = self.create()
        native_id, path = self.native(record)
        original = path.read_bytes()
        other = self.base / "other.jsonl"
        other.write_bytes(original)
        for link in (lambda: path.symlink_to(other), lambda: os.link(other, path)):
            path.unlink()
            link()
            with self.assertRaises(ValueError):
                cli.transcript("codex", cli.project_home(self.store.meta(self.pid), "codex"),
                               native_id, self.store.root(self.pid))

    def test_parent_symlink_transcript_rejected(self):
        record = self.create("claude")
        native_id, path = self.native(record)
        moved = self.base / "moved"
        path.parent.rename(moved)
        path.parent.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            cli.read_transcript(path)

    def test_private_home_failure_never_falls_back(self):
        root = cli.project_home(self.store.meta(self.pid), "codex")
        with patch.object(cli, "refresh_links", return_value=["auth.json"]):
            with self.assertRaises(SessionError) as ctx:
                cli.home(self.store.meta(self.pid), "codex")
        self.assertEqual(ctx.exception.code, "cli_home_unavailable")
        self.assertTrue(root.exists())

    def test_private_auth_copy_never_syncs_back_or_overwrites_new_host(self):
        host = cli.host_home("codex")
        host.mkdir()
        auth = host / "auth.json"
        auth.write_text('{"fake":"host-first"}')
        root, _ = cli.home(self.store.meta(self.pid), "codex")
        self.assertFalse((root / "auth.json").is_symlink())
        (root / "auth.json").write_text('{"fake":"private-refresh"}')
        cli.home(self.store.meta(self.pid), "codex")
        self.assertEqual(auth.read_text(), '{"fake":"host-first"}')
        self.assertIn("private-refresh", (root / "auth.json").read_text())
        auth.write_text('{"fake":"new-host-login"}')
        cli.home(self.store.meta(self.pid), "codex")
        self.assertEqual((root / "auth.json").read_text(), auth.read_text())

    def test_claude_config_inheritance_is_filtered_copy_skills_remain(self):
        host = cli.host_home("claude")
        host.mkdir()
        (host / "skills").mkdir()
        (host / ".claude.json").write_text(json.dumps({"mcpServers": {"existing": {"command": "fake"}},
                                                        "projects": {"unrelated": {}}, "hasCompletedOnboarding": True}))
        root, _ = cli.home(self.store.meta(self.pid), "claude")
        self.assertFalse((root / ".claude.json").is_symlink())
        self.assertTrue((root / "skills").is_dir())
        inherited = json.loads((root / ".claude.json").read_text())
        self.assertIn("existing", inherited["mcpServers"])
        self.assertNotIn("projects", inherited)

    def test_terminal_first_turn_identity_history_and_chat_resume(self):
        for engine in cli.ENGINES:
            record = self.create(engine)
            sid = record["id"]
            with patch.object(term, "unavailable_reason", return_value=""), patch.object(cli, "executable", return_value=engine):
                self.sessions.terminal(self.pid, sid)
            args = self.sessions.terminals.calls[-1]["argv"]
            if engine == "claude":
                self.assertEqual(args[args.index("--session-id") + 1], record["native_id"])
            native_id, path = self.native(record)
            live = self.sessions.get(self.pid, sid)["session"]
            self.assertEqual(live["native_id"], native_id)
            self.assertEqual(len(live["messages"]), 2)
            self.sessions.terminals.close_scope(self.pid, sid)
            cli.write_transcript(path, transcript(engine, native_id, self.store.root(self.pid), 2))
            closed = self.sessions.get(self.pid, sid)["session"]
            self.assertEqual(len(closed["messages"]), 4)
            self.assertNotIn("terminal_tracking", closed)
            if engine == "codex":
                # Codex now resumes via App Server; the fake stdio tests cover that RPC.
                with patch.object(self.sessions.native, "send", return_value={"session": closed}) as send:
                    self.sessions.send(self.pid, sid, {"message": "Continue this same session"})
                self.assertEqual(send.call_args.args[:2], (self.pid, sid))
                called_record = closed
            else:
                with patch.object(self.sessions, "_run") as run, patch.object(cli, "executable", return_value=engine):
                    self.sessions.send(self.pid, sid, {"message": "Continue this same session"})
                    self.wait_idle()
                called_record = run.call_args.args[1]
            self.assertEqual(called_record["native_id"], native_id)
            self.assertTrue(cli.transcript(engine, self.sessions._native_home(self.pid, called_record),
                                           native_id, self.store.root(self.pid)))

    def test_fresh_codex_terminal_cannot_be_ambiguous(self):
        one, two = self.create(), self.create()
        with patch.object(term, "unavailable_reason", return_value=""), patch.object(cli, "executable", return_value="codex"):
            self.sessions.terminal(self.pid, one["id"])
            with self.assertRaises(SessionError):
                self.sessions.terminal(self.pid, two["id"])
            with self.assertRaises(SessionError):
                self.sessions.send(self.pid, two["id"], {"message": "cannot start yet"})
            self.native(one)
            self.sessions.get(self.pid, one["id"])
            self.sessions.terminal(self.pid, two["id"])

    def test_terminal_chat_mutual_exclusion(self):
        record = self.create("claude")
        with patch.object(term, "unavailable_reason", return_value=""), patch.object(cli, "executable", return_value="claude"):
            self.sessions.terminal(self.pid, record["id"])
            with self.assertRaises(SessionError):
                self.sessions.send(self.pid, record["id"], {"message": "blocked"})
        self.assertEqual(self.sessions.get(self.pid, record["id"])["session"]["messages"], [])

    def test_controlled_process_persists_answer_and_native_id(self):
        record = self.create("claude")
        proc = ControlledProcess(cli.new_native_id("codex"))
        with patch.object(cli, "executable", return_value="fake"), patch("easyread.workspaces.sessions.subprocess.Popen", return_value=proc):
            self.sessions.send(self.pid, record["id"], {"message": "hello"})
            self.wait_idle()
        result = self.sessions.get(self.pid, record["id"])["session"]
        self.assertEqual(result["state"], "idle")
        self.assertEqual(result["messages"][-1]["content"], "controlled answer")
        self.assertTrue(result["native_id"])
        self.assertEqual(result["usage"]["input"], 4)

    def test_job_stays_busy_until_finish_is_persisted(self):
        record = self.create("claude")
        entered, release = threading.Event(), threading.Event()
        original = self.sessions._finish
        def finish(*args):
            entered.set()
            release.wait(5)
            return original(*args)
        proc = ControlledProcess(cli.new_native_id("codex"))
        with patch.object(cli, "executable", return_value="fake"), patch("easyread.workspaces.sessions.subprocess.Popen", return_value=proc), patch.object(self.sessions, "_finish", side_effect=finish):
            self.sessions.send(self.pid, record["id"], {"message": "hello"})
            self.assertTrue(entered.wait(3))
            try:
                self.assertTrue(self.sessions.busy(self.pid))
                with self.assertRaises(SessionError):
                    self.sessions.send(self.pid, record["id"], {"message": "racing"})
                with self.assertRaises(SessionError):
                    self.sessions.export_state(self.pid, self.base / "busy-export")
            finally:
                release.set()
            self.wait_idle()
        self.assertEqual(self.sessions.get(self.pid, record["id"])["session"]["state"], "idle")

    def test_cancel_waits_for_thread_and_persists_interrupted(self):
        record = self.create("claude")
        proc = ControlledProcess(cli.new_native_id("codex"), threading.Event())
        spawned = threading.Event()
        def spawn(*args, **kwargs):
            spawned.set()
            return proc
        with patch.object(cli, "executable", return_value="fake"), patch("easyread.workspaces.sessions.subprocess.Popen", side_effect=spawn), patch.object(cli, "stop_process", side_effect=lambda p: p.stop()):
            self.sessions.send(self.pid, record["id"], {"message": "hello"})
            self.assertTrue(spawned.wait(3))
            result = self.sessions.cancel(self.pid, record["id"])
        self.assertFalse(self.sessions.busy(self.pid))
        self.assertEqual(result["session"]["state"], "interrupted")

    def test_cancel_and_delete_refuse_live_thread(self):
        from easyread.workspaces.sessions import _Job
        record = self.create()
        job = _Job(self.pid, record["id"])
        class LiveThread:
            def is_alive(self): return True
            def join(self, timeout=None): pass
        job.thread = LiveThread()
        self.sessions._jobs[(self.pid, record["id"])] = job
        try:
            for operation in (self.sessions.cancel, self.sessions.delete):
                with self.assertRaises(SessionError):
                    operation(self.pid, record["id"])
            self.assertTrue(self.sessions.busy(self.pid))
            self.assertIn(record["id"], self.sessions._load(self.pid)["sessions"])
        finally:
            self.sessions._jobs.clear()

    def test_send_does_not_hold_session_lock_while_waiting_for_store(self):
        record = self.create("claude")
        attempted = threading.Event()
        original = self.store.lock
        def lock(pid):
            if threading.current_thread().name == "send-test":
                attempted.set()
            return original(pid)
        errors = []
        def send():
            try:
                self.sessions.send(self.pid, record["id"], {"message": "hello"})
            except Exception as error:
                errors.append(error)
        with patch.object(self.store, "lock", side_effect=lock), patch.object(cli, "executable", return_value="fake"), patch.object(self.sessions, "_run"):
            with original(self.pid):
                thread = threading.Thread(target=send, name="send-test", daemon=True)
                thread.start()
                self.assertTrue(attempted.wait(2))
                acquired = self.sessions._lock.acquire(timeout=1)
                self.assertTrue(acquired, "lock inversion: send acquired Sessions lock first")
                if acquired:
                    self.sessions._lock.release()
                self.assertTrue(self.sessions.busy(self.pid))  # Admission is reserved, not a project lock.
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertFalse(errors)

    def test_export_import_full_history_private_no_auth_mutation(self):
        for engine in cli.ENGINES:
            record = self.create(engine)
            native_id, _ = self.native(record)
            def fill(data):
                r = data["sessions"][record["id"]]
                r["native_id"] = native_id
                r["reasoning_effort"] = "high"
                r["messages"] = [{"role": "user", "content": str(i)} for i in range(KEEP_MESSAGES + 20)]
                r["events"] = [{"kind": "test", "text": str(i)} for i in range(KEEP_EVENTS + 30)]
            self.sessions._commit(self.pid, fill)
        destination = self.base / "export"
        with patch.object(cli, "home", side_effect=AssertionError("must not prepare auth")):
            result = self.sessions.export_state(self.pid, destination)
            self.assertTrue(result["native_complete"])
            new_pid = self.store.allocate({"name": "Imported"})["id"]
            with patch.dict(os.environ, {"EASYREAD_WORKSPACE_CLI_ISOLATION": "0"}):
                imported = self.sessions.import_state(new_pid, destination)
        self.assertEqual(imported["native"], 2)
        data = self.sessions._load(new_pid, full=True)
        for record in data["sessions"].values():
            self.assertEqual(len(record["messages"]), KEEP_MESSAGES + 20)
            self.assertEqual(len(record["events"]), KEEP_EVENTS + 30)
            self.assertEqual(record["reasoning_effort"], "high")
            self.assertTrue(record["cli_isolated"])
            path = cli.transcript(record["engine"], self.sessions._native_home(new_pid, record),
                                  record["native_id"], self.store.root(new_pid))
            self.assertIsNotNone(path)
            text = cli.read_transcript(path)
            self.assertIn(str(self.store.root(new_pid)), text)
            self.assertNotIn(str(self.store.root(self.pid)), text)
        self.assertEqual(sorted(p.name for p in destination.iterdir()), ["manifest.json", "native"])

    def test_import_rejects_version_types_ids_and_malformed_transcripts(self):
        record = self.create()
        native_id, _ = self.native(record)
        self.sessions._commit(self.pid, lambda data: data["sessions"][record["id"]].update(native_id=native_id))
        dest = self.base / "export"
        self.sessions.export_state(self.pid, dest)
        manifest_path = dest / "manifest.json"
        original = json.loads(manifest_path.read_text())
        for change in (lambda m: m.update(version=99), lambda m: m["sessions"][0].update(messages="wrong"),
                       lambda m: m["sessions"][0].update(native_id=str(uuid.uuid4())),
                       lambda m: m["sessions"][0].update(engine="other")):
            manifest = json.loads(json.dumps(original))
            change(manifest)
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                self.sessions.import_state(self.pid, dest)
        manifest_path.write_text(json.dumps(original))
        (dest / original["sessions"][0]["native"]).write_text('{"type":"session_meta"}\nnot-json\n')
        with self.assertRaises(ValueError):
            self.sessions.import_state(self.pid, dest)
        self.assertEqual(len(self.sessions._load(self.pid)["sessions"]), 1)

    def test_parser_retains_long_event_content_and_relocates_uuid(self):
        long = "x" * 12000
        stream = cli.Stream("codex")
        events = stream.feed(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": long}}))
        self.assertEqual(events[0]["text"], long)
        for engine in cli.ENGINES:
            old, new = cli.new_native_id(engine), cli.new_native_id(engine)
            relocated = cli.relocate(engine, transcript(engine, old, self.base / "old"), new, self.base / "new")
            self.assertNotIn(old, relocated)
            cli.validate_transcript(engine, relocated, new)
            self.assertEqual(len(cli.transcript_messages(engine, relocated)), 2)

    def test_windows_kill_uses_tree_taskkill(self):
        proc = ControlledProcess(cli.new_native_id("codex"))
        with patch.object(cli.os, "name", "nt"), patch.object(cli.subprocess, "run") as run:
            cli.stop_process(proc)
        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", str(proc.pid), "/T", "/F"])

    @unittest.skipIf(os.name == "nt", "POSIX process group regression")
    def test_stop_process_kills_reaped_leader_group(self):
        calls = []
        proc = ControlledProcess(cli.new_native_id("codex"))
        proc.returncode = 0
        with patch.object(cli.os, "killpg", side_effect=lambda pid, sig: calls.append((pid, sig))):
            cli.stop_process(proc)
        self.assertEqual([sig for _, sig in calls], [cli.signal.SIGTERM, cli.signal.SIGKILL])

    def test_mcp_project_validation_reader_error_and_tools(self):
        try:
            from mcp.server.fastmcp import FastMCP  # noqa: F401
        except ImportError:
            self.skipTest("Optional MCP dependency is not installed")
        server = mcp.build(self.store.libroot, self.pid, store=self.store)
        tools = asyncio.run(server.list_tools())
        names = {tool.name for tool in tools}
        self.assertEqual(names, set(mcp.DESCRIPTIONS))
        result = asyncio.run(server.call_tool("get_document", {"document_id": "missing"}))
        self.assertTrue(result.isError)
        self.assertIn("not_found", str(result))
        content = asyncio.run(server.call_tool("get_content", {"document_id": "missing", "language": "wrong"}))
        self.assertTrue(content.isError)
        with self.assertRaises(ValueError):
            mcp.build(self.store.libroot, "z" * 20, store=self.store)

    def test_codex_0169_transcript_item_completed_messages(self):
        native_id = cli.uuid7()
        records = [{"type": "session_meta", "payload": {"id": native_id, "session_id": native_id,
                                                         "cwd": str(self.base)}},
                   {"type": "response_item", "payload": {"type": "message", "role": "developer",
                                                           "content": [{"type": "input_text", "text": "not a user message"}]}},
                   {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                       "type": "UserMessage", "id": "u1", "content": [{"type": "text", "text": "native question"}]}}},
                   {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                       "type": "AgentMessage", "id": "a1", "content": [{"type": "text", "text": "native answer"}],
                       "phase": "final_answer"}}}]
        raw = "".join(json.dumps(r) + "\n" for r in records)
        messages = cli.transcript_messages("codex", raw + '{"incomplete":')
        self.assertEqual([(m["role"], m["content"]) for m in messages],
                         [("user", "native question"), ("assistant", "native answer")])
        with self.assertRaises(SessionError):
            cli.validate_transcript("codex", raw + '{"incomplete":')

    def test_codex_mcp_passes_pythonpath_to_stdio_child(self):
        args = cli.project_mcp_args("codex", self.store.libroot, self.pid)
        self.assertIn('mcp_servers.easyread_project.env_vars=["PYTHONPATH"]', args)
        self.assertNotIn("--strict-mcp-config", args)

    def test_failed_final_persistence_remains_busy(self):
        record = self.create("claude")
        proc = ControlledProcess(cli.new_native_id("codex"))
        with patch.object(cli, "executable", return_value="fake"), patch("easyread.workspaces.sessions.subprocess.Popen", return_value=proc), patch.object(self.sessions, "_finish", side_effect=OSError("disk unavailable")):
            self.sessions.send(self.pid, record["id"], {"message": "hello"})
            job = self.sessions._jobs[(self.pid, record["id"])]
            job.thread.join(3)
        try:
            self.assertFalse(job.thread.is_alive())
            self.assertTrue(job.finalization_error)
            self.assertTrue(self.sessions.busy(self.pid))
            with self.assertRaises(SessionError):
                self.sessions.delete(self.pid, record["id"])
        finally:
            self.sessions._jobs.clear()

    def test_mcp_reads_content_and_links_without_opening_paper(self):
        try:
            from mcp.server.fastmcp import FastMCP  # noqa: F401
        except ImportError:
            self.skipTest("Optional MCP dependency is not installed")
        from easyread.library.library import Library
        library = Library(self.store.libroot)
        ws, _ = library.create_from_pdf(b"%PDF-test", "fixture.pdf", meta={"title_en": "Disposable research fixture"})
        ws.update("paper", lambda p: p.update(blocks=[{"id": "p1", "type": "para", "en": "Original fixture"}]))
        versions = ws.versions()
        api = mcp.public_api(self.store.libroot)
        self.assertEqual(api.documents(q="Disposable")["total"], 1)
        self.assertEqual(api.content(ws.id)["items"][0]["en"], "Original fixture")
        self.assertEqual(versions, ws.versions())
        server = mcp.build(self.store.libroot, self.pid, store=self.store, api=api)
        asyncio.run(server.call_tool("link_paper", {"document_id": ws.id}))
        self.assertIn(ws.id, self.store.get(self.pid)["papers"])
        asyncio.run(server.call_tool("unlink_paper", {"document_id": ws.id}))
        self.assertNotIn(ws.id, self.store.get(self.pid)["papers"])

    def test_terminal_stop_retains_reservation_on_failure(self):
        terminal = term.NativeTerminals(type("Access", (), {"valid": lambda self, sid: True})())
        terminal.sessions["key"] = {"proc": ControlledProcess(cli.new_native_id("codex")), "sockets": set()}
        with patch.object(cli, "stop_process", side_effect=SessionError("conflict", "still alive")):
            with self.assertRaises(SessionError):
                terminal._stop("key")
        self.assertIn("key", terminal.sessions)


if __name__ == "__main__":
    unittest.main()

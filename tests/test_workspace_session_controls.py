"""Session effort and terminal/settings admission; no native/model execution."""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from easyread.workspaces import model_catalog, session_cli as cli, session_reasoning as reasoning
from easyread.workspaces.sessions import Sessions, SessionError
from easyread.workspaces.store import Store
from tests.test_workspace_sessions import FakeTerminals


class WorkspaceSessionControlsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.store = Store(self.base / "library")
        self.pid = self.store.allocate({"name": "Controls"})["id"]
        self.sessions = Sessions(self.store)
        self.sessions.terminals = FakeTerminals()
        self.addCleanup(self.sessions.close)
        env = patch.dict(os.environ, {"CODEX_HOME": str(self.base / "codex"), "CLAUDE_CONFIG_DIR": str(self.base / "claude")})
        env.start(); self.addCleanup(env.stop)
        catalog = patch.object(model_catalog, "listing", side_effect=lambda store, pid, engine: {
            "models": [{"id": "limited", "reasoning_levels": ["low", "high"]}],
            "default": "limited", "reasoning_levels": list(reasoning.LEVELS[engine])})
        catalog.start(); self.addCleanup(catalog.stop)

    def create(self, engine="codex", **body):
        return self.sessions.create(self.pid, {"engine": engine, **body})["session"]

    def test_persist_settings_legacy_inheritance_model_validation_and_native_identity(self):
        for engine in ("codex", "claude"):  # Pi requires provider/id; its controls have dedicated tests.
            record = self.create(engine, model="custom", reasoning_effort="high")
            sid, native_id = record["id"], record["native_id"]
            self.sessions._commit(self.pid, lambda data: data["sessions"][sid]["messages"].append({"role": "user", "content": "history"}))
            updated = self.sessions.settings(self.pid, sid, {"reasoning_effort": ""})
            self.assertEqual(updated["session"]["model"], "custom")
            self.assertEqual(updated["session"]["reasoning_effort"], "")
            self.assertEqual(updated["session"]["native_id"], native_id)
            self.assertEqual(updated["session"]["engine"], engine)
            self.assertEqual(updated["session"]["messages"][0]["content"], "history")
            self.assertIn("cursor", updated)
            self.sessions.settings(self.pid, sid, {"reasoning_effort": "xhigh"})
            with self.assertRaises(SessionError):
                self.sessions.settings(self.pid, sid, {"model": "limited"})
            self.assertEqual(self.sessions.get(self.pid, sid)["session"]["model"], "custom")
            restarted = Sessions(self.store)
            try:
                self.assertEqual(restarted.get(self.pid, sid)["session"]["reasoning_effort"], "xhigh")
            finally:
                restarted.close()
            self.sessions._commit(self.pid, lambda data: data["sessions"][sid].pop("reasoning_effort"))
            self.assertEqual(self.sessions.get(self.pid, sid)["session"]["reasoning_effort"], "")
            for bad in (None, 1, "High", "unsafe value", "a" * 33 if engine == "codex" else "minimal"):
                with self.subTest(engine=engine, bad=bad), self.assertRaises(SessionError):
                    self.sessions.settings(self.pid, sid, {"reasoning_effort": bad})
            with self.assertRaises(SessionError):
                self.sessions.settings(self.pid, sid, {"engine": "claude"})
        with self.assertRaises(SessionError):
            self.create(reasoning_effort="xhigh")  # Known default model only allows low/high.

    def test_idle_guards_do_not_stop_work(self):
        record = self.create()
        sid = record["id"]
        flags = [{"state": state} for state in ("running", "queued", "waiting_user", "waiting_external")]
        flags += [{key: True} for key in ("background_active", "native_active", "queued")]
        flags += [{"pending_requests": [{"id": "pending"}]}]
        flags += [{"goal": {"status": status}} for status in ("active", "blocked", "usageLimited", "budgetLimited")]
        for fields in flags:
            self.sessions._commit(self.pid, lambda data: data["sessions"][sid].update(fields))
            with self.subTest(fields=fields), patch.object(self.sessions.native, "stop") as stop:
                with self.assertRaises(SessionError) as error:
                    self.sessions.settings(self.pid, sid, {"model": "new"})
                self.assertEqual(error.exception.status, 409)
                stop.assert_not_called()
            def reset(data):
                for key in fields:
                    data["sessions"][sid].pop(key, None)
                data["sessions"][sid]["state"] = "idle"
            self.sessions._commit(self.pid, reset)

    def test_released_sessions_allow_settings_and_terminal_without_clearing_history(self):
        for state, goal in (("interrupted", None), ("error", None),
                            ("completed", {"status": "complete"}), ("paused", {"status": "paused"})):
            with self.subTest(state=state):
                record = self.create(model="custom", reasoning_effort="high")
                sid = record["id"]
                historical = {"state": state, "goal": goal, "reconciliation_required": True,
                              "pending_requests": [{"id": "old", "expired": True}]}
                self.sessions._commit(self.pid, lambda data: data["sessions"][sid].update(historical))
                updated = self.sessions.settings(self.pid, sid, {"reasoning_effort": "low"})["session"]
                for key, value in historical.items():
                    self.assertEqual(updated[key], value)
                self.assertEqual(updated["native_id"], record["native_id"])
                with patch("easyread.workspaces.session_terminal.unavailable_reason", return_value=""), \
                        patch.object(cli, "executable", return_value="fake"):
                    self.sessions.terminal(self.pid, sid, access_sid="owner")
                self.assertTrue(self.sessions.get(self.pid, sid)["session"]["terminal_open"])
                self.sessions.terminals.close_scope(self.pid, sid)

    def test_settings_reservation_blocks_send_goal_terminal_but_not_readers_during_shutdown(self):
        sid = self.create("claude")["id"]
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        def stop(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Shutdown was not released")
        def update():
            try:
                results.append(self.sessions.settings(self.pid, sid, {"reasoning_effort": "high"}))
            except Exception as error:
                errors.append(error)
        with patch.object(self.sessions.native, "stop", side_effect=stop):
            worker = threading.Thread(target=update)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.sessions.get(self.pid, sid)["session"]["reasoning_effort"], "")
                for operation in (lambda: self.sessions.send(self.pid, sid, {"message": "no"}),
                                  lambda: self.sessions.goal(self.pid, sid, {"action": "set", "objective": "no"}),
                                  lambda: self.sessions.terminal(self.pid, sid),
                                  lambda: self.sessions.settings(self.pid, sid, {"model": "other"})):
                    with self.assertRaises(SessionError) as error:
                        operation()
                    self.assertEqual(error.exception.status, 409)
            finally:
                release.set(); worker.join(3)
        self.assertFalse(worker.is_alive()); self.assertFalse(errors)
        self.assertEqual(results[0]["session"]["reasoning_effort"], "high")
        self.assertFalse(self.sessions._operations)

    def test_owned_terminal_reuse_is_read_only_and_active_work_is_not_stopped(self):
        record = self.create("claude", model="custom", reasoning_effort="max")
        sid = record["id"]
        with patch("easyread.workspaces.session_terminal.unavailable_reason", return_value=""), \
                patch.object(cli, "executable", return_value="fake"):
            first = self.sessions.terminal(self.pid, sid, access_sid="owner")
        self.assertIn("--effort", self.sessions.terminals.calls[0]["argv"])
        with patch.object(self.sessions.native, "stop", side_effect=AssertionError("Do not stop on reuse")), \
                patch.object(cli, "launch_env", side_effect=AssertionError("Do not mutate native settings")):
            second = self.sessions.terminal(self.pid, sid, access_sid="owner")
            self.assertEqual(first["url"], second["url"])
            self.assertEqual(len(self.sessions.terminals.calls), 1)
            self.assertTrue(self.sessions.get(self.pid, sid)["session"]["terminal_open"])
            with self.assertRaises(SessionError):
                self.sessions.settings(self.pid, sid, {"model": "new"})
            with patch.object(self.sessions.terminals, "scope_url", side_effect=ValueError("other access")):
                with self.assertRaises(SessionError) as error:
                    self.sessions.terminal(self.pid, sid, access_sid="other")
                self.assertEqual(error.exception.status, 409)
        self.sessions.terminals.close_scope(self.pid, sid)
        self.sessions._commit(self.pid, lambda data: data["sessions"][sid].update(state="running"))
        with patch.object(self.sessions.native, "stop") as stop:
            with self.assertRaises(SessionError):
                self.sessions.terminal(self.pid, sid)
            stop.assert_not_called()

    def test_exact_effort_argv_for_both_engines_and_inherit_omits_override(self):
        for engine in ("codex", "claude"):
            for value in ("", *reasoning.LEVELS[engine]):
                for resume in (False, True):
                    with patch.object(cli, "executable", return_value="fake"):
                        args = cli.argv(engine, exe="fake", prompt_stdin=True, native_id="id", resume=resume,
                                        reasoning_effort=value)
                        tui = cli.terminal_argv(engine, "id", resume=resume, reasoning_effort=value)
                    for command in (args, tui):
                        if engine == "claude":
                            self.assertEqual("--effort" in command, bool(value))
                            if value:
                                self.assertEqual(command[command.index("--effort") + 1], value)
                        else:
                            self.assertEqual([a for a in command if a.startswith("model_reasoning_effort=")],
                                             ['model_reasoning_effort="' + value + '"'] if value else [])

    def test_native_defaults_are_metadata_only_model_aware_and_never_guessed(self):
        from unittest.mock import Mock
        read = Mock(side_effect=[{'config': {}}, {'data': [
            {'model': 'default-model', 'isDefault': True, 'defaultReasoningEffort': 'ultra'},
            {'model': 'explicit-model', 'defaultReasoningEffort': 'high'}]}])
        self.assertEqual(reasoning.codex_defaults(read, '', ''), ('default-model', 'ultra'))
        self.assertEqual([c.args[0] for c in read.call_args_list], ['config/read', 'model/list'])
        read.reset_mock(side_effect=True)
        read.side_effect = [{'config': {'model': 'default-model'}}, {'data': [
            {'model': 'explicit-model', 'defaultReasoningEffort': 'high'}]}]
        self.assertEqual(reasoning.codex_defaults(read, 'explicit-model', ''), ('explicit-model', 'high'))
        read.side_effect = [{'config': {'model': 'default-model', 'model_reasoning_effort': 'low'}}]
        self.assertEqual(reasoning.codex_defaults(read, '', ''), ('default-model', 'low'))
        read.side_effect = [{'config': {}}, {'data': []}]
        with self.assertRaises(SessionError) as error:
            reasoning.codex_defaults(read, 'custom-without-metadata', '')
        self.assertEqual((error.exception.code, error.exception.status), ('defaults_unavailable', 409))
        read.side_effect = AssertionError('Explicit settings need no metadata')
        self.assertEqual(reasoning.codex_defaults(read, 'custom', 'xhigh'), ('custom', 'xhigh'))

    def test_resumed_tui_uses_real_defaults_without_changing_persisted_inheritance(self):
        from tests.test_workspace_sessions import transcript
        record = self.create()
        sid, native_id = record['id'], cli.new_native_id('codex')
        home = cli.project_home(self.store.meta(self.pid), 'codex')
        root = self.store.root(self.pid)
        cli.write_transcript(cli.transcript_target('codex', home, native_id, root), transcript('codex', native_id, root))
        self.sessions._commit(self.pid, lambda data: data['sessions'][sid].update(
            native_id=native_id, native_started=True, reconciliation_required=True))
        self.sessions.settings(self.pid, sid, {'model': '', 'reasoning_effort': ''})
        self.assertTrue(self.sessions.get(self.pid, sid)['session']['reconciliation_required'])
        with patch('easyread.workspaces.session_terminal.unavailable_reason', return_value=''), \
                patch.object(cli, 'executable', return_value='fake'), \
                patch('easyread.workspaces.codex_model_list.defaults', return_value=('default-model', 'ultra')) as defaults:
            self.sessions.terminal(self.pid, sid, access_sid='owner')
            defaults.assert_called_once()
            self.assertEqual(defaults.call_args.args[1:3], (home, root))
            self.sessions.terminal(self.pid, sid, access_sid='owner')
            defaults.assert_called_once()  # Reopening a view is not a new process/probe.
        args = self.sessions.terminals.calls[0]['argv']
        self.assertIn(native_id, args)
        self.assertEqual(args[args.index('--model') + 1], 'default-model')
        self.assertIn('model_reasoning_effort="ultra"', args)
        snapshot = self.sessions.get(self.pid, sid)['session']
        self.assertEqual((snapshot['model'], snapshot['reasoning_effort']), ('', ''))

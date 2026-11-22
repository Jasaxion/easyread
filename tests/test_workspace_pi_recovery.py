"""Synthetic Pi v3 transcript compatibility/recovery; no native data or providers."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from easyread.workspaces import pi_sessions as pi, session_archive, session_cli as cli
from easyread.workspaces import session_terminal as term
from easyread.workspaces.sessions import Sessions, SessionError
from tests import test_workspace_sessions as fixtures
from tests import test_workspace_pi as pi_fixtures


def encode(rows):
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def native_rows(native_id, cwd):
    """Match the reported 14-row shape, not the user's actual transcript."""
    rows = [{"type": "session", "version": 3, "id": native_id, "cwd": str(cwd),
             "parentSession": "/foreign/fork.jsonl", "timestamp": "2026-01-01T00:00:00Z"}]
    entries = [{"type": "model_change", "provider": "test", "modelId": "test"},
               {"type": "thinking_level_change", "thinkingLevel": "high"},
               {"type": "session_info", "name": "Synthetic CLI update"}]
    messages = [{"role": "system", "content": "PRIVATE-SYSTEM", "sections": {"secret": "PRIVATE-SECTION"}},
                {"role": "user", "content": "Public question"}]
    for index in range(4):
        messages.append({"role": "assistant", "content": [
            {"type": "text", "text": f"Public answer {index}\u2028text", "textSignature": "PRIVATE-SIGNATURE"},
            {"type": "thinking", "thinking": "PRIVATE-THINKING", "thinkingSignature": str(cwd) + "/signed"}]})
        if index < 3:
            messages.append({"role": "toolResult", "toolCallId": f"tool{index}", "toolName": "read",
                             "content": [{"type": "text", "text": "PRIVATE-TOOL"}], "isError": False})
    messages.append({"role": "bashExecution", "command": f"echo {cwd}/native-command", "output": "PRIVATE-SHELL " + str(cwd),
                     "exitCode": 0, "cancelled": False, "truncated": False, "excludeFromContext": True, "timestamp": 1})
    entries += [{"type": "message", "message": message} for message in messages]
    parent = None
    for index, entry in enumerate(entries):
        entry.update(id=f"entry{index}", parentId=parent, timestamp="2026-01-01T00:00:00Z")
        rows.append(entry)
        parent = entry["id"]
    return rows


class PiRecoveryTest(unittest.TestCase):
    setUp = fixtures.WorkspaceSessionsTest.setUp
    create = pi_fixtures.PiWorkspaceTest.create
    native = fixtures.WorkspaceSessionsTest.native
    wait_idle = fixtures.WorkspaceSessionsTest.wait_idle

    def fixture(self):
        record = self.create()
        _, path = self.native(record)
        rows = native_rows(record["native_id"], self.store.root(self.pid))
        path.write_text(encode(rows), encoding="utf-8")
        return record, path, rows

    def track(self, record, count=0):
        self.sessions._commit(self.pid, lambda data: data["sessions"][record["id"]].update(
            terminal_tracking={"baseline": [], "message_count": count}))

    def listing(self):
        with patch.object(self.sessions, "capabilities", return_value={"engines": {engine: {} for engine in cli.ENGINES}}), \
                patch.object(cli, "engine_status", side_effect=lambda engine, _: {"id": engine, "capabilities": {}}):
            return self.sessions.listing(self.pid)["sessions"]

    def test_bash_execution_14_rows_reopen_list_and_continue_exact_identity(self):
        record, path, rows = self.fixture()
        original = path.read_bytes()
        self.assertEqual(len(rows), 14)
        self.assertEqual(pi.validate(original.decode()), rows)
        self.assertNotIn("content", rows[-1]["message"])
        self.track(record)
        self.assertEqual(len(self.listing()), 1)
        result = self.sessions.get(self.pid, record["id"])["session"]
        self.assertEqual(len(result["messages"]), 5)
        self.assertNotIn("PRIVATE-", json.dumps(result))
        self.assertNotIn("native_sync_warning", result)
        self.assertEqual(path.read_bytes(), original)
        reopened = Sessions(self.store)
        reopened.terminals = fixtures.FakeTerminals()
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.get(self.pid, record["id"])["session"]["native_id"], record["native_id"])
        with patch.object(term, "unavailable_reason", return_value=""), patch.object(cli, "executable", return_value="fake-pi"):
            reopened.terminal(self.pid, record["id"])
        args = reopened.terminals.calls[-1]["argv"]
        self.assertEqual(args[args.index("--session") + 1], str(path))
        reopened.terminals.close_scope(self.pid, record["id"])
        reopened.get(self.pid, record["id"])
        script = self.base / "fake_pi.py"
        script.write_text(pi_fixtures.FAKE_PI)
        popen, calls = subprocess.Popen, []
        def start(args, **kwargs):
            calls.append(args)
            return popen([sys.executable, str(script), *args[1:]], **kwargs)
        with patch.object(cli, "executable", return_value="fake-pi"), \
                patch("easyread.workspaces.sessions.subprocess.Popen", side_effect=start):
            self.sessions.send(self.pid, record["id"], {"message": "Continue this conversation"})
            self.wait_idle()
        self.assertEqual(calls[0][calls[0].index("--session") + 1], str(path))
        self.assertNotIn("--session-id", calls[0])
        self.assertEqual(pi.validate(path.read_text())[:14], rows)
        self.assertEqual(self.sessions.get(self.pid, record["id"])["session"]["state"], "idle")

    def test_native_variants_and_metadata_roundtrip_without_rewriting_command_output(self):
        record, path, rows = self.fixture()
        variants = [
            {"type": "message", "message": {"role": "custom", "customType": "extension", "display": True,
                 "content": "PRIVATE-CUSTOM", "details": {"path": str(self.store.root(self.pid))}}},
            {"type": "message", "message": {"role": "branchSummary", "summary": "PRIVATE-BRANCH", "fromId": None}},
            {"type": "message", "message": {"role": "compactionSummary", "summary": "PRIVATE-COMPACTION", "tokensBefore": 42}},
            {"type": "compaction", "summary": "PRIVATE-SUMMARY", "firstKeptEntryId": "entry0", "tokensBefore": 42},
            {"type": "branch_summary", "summary": "PRIVATE-SUMMARY", "fromId": "entry0"},
            {"type": "custom", "customType": "extension", "data": {"unknown": [1, 2]}},
            {"type": "custom_message", "customType": "extension", "display": False, "content": "PRIVATE-CUSTOM"},
            {"type": "context_edit", "targetId": "entry0", "replacement": None},
        ]
        for index, row in enumerate(variants):
            row.update(id=f"variant{index}", parentId=rows[-1]["id"])
            rows.append(row)
        original = encode(rows).replace("\n", "\r\n").encode()
        path.write_bytes(original)
        self.track(record)
        result = self.sessions.get(self.pid, record["id"])["session"]
        self.assertNotIn("PRIVATE-", json.dumps(result))
        destination = self.base / "export"
        self.sessions.export_state(self.pid, destination)
        exported = next((destination / "native" / "pi" / "sessions").glob("*.jsonl"))
        self.assertEqual(exported.read_bytes(), original)
        imported_pid = self.store.allocate({"name": "Imported Pi", "engine": "pi"})["id"]
        result = self.sessions.import_state(imported_pid, destination)
        self.assertEqual(result["native"], 1)
        target = cli.transcript("pi", cli.project_home(self.store.meta(imported_pid), "pi"),
                                record["native_id"], self.store.root(imported_pid))
        imported = pi.validate(target.read_text())
        self.assertEqual(imported[0]["cwd"], str(self.store.root(imported_pid)))
        self.assertEqual(imported[0]["id"], record["native_id"])
        self.assertNotIn("parentSession", imported[0])
        self.assertEqual(imported[1:], rows[1:])
        self.assertEqual(target.read_bytes().split(b"\n", 1)[1], original.split(b"\n", 1)[1])
        self.assertEqual(path.read_bytes(), original)

    def test_role_aware_validation_does_not_accept_malformed_chat_or_shell(self):
        _, _, rows = self.fixture()
        broken = [{"role": "user"}, {"role": "assistant", "content": {}},
                  {"role": "user", "content": ["text"]}, {"role": "assistant", "content": [{"type": "text", "text": 2}]},
                  {"role": "assistant", "content": [{"type": "toolCall", "id": "call", "name": "read", "arguments": []}]},
                  {"role": "user", "content": [{"type": "thinking", "thinking": "private"}]},
                  {"role": "unknown", "content": "not a native role"}, {"role": [], "content": "bad"},
                  {"role": "bashExecution", "output": "missing command"},
                  {**rows[-1]["message"], "cancelled": "false"}, {**rows[-1]["message"], "output": []}]
        for message in broken:
            with self.subTest(message=message):
                changed = deepcopy(rows)
                changed[-1]["message"] = message
                with self.assertRaises(SessionError):
                    pi.validate(encode(changed))
                with self.assertRaises(SessionError):
                    pi.messages(encode(changed))
        cancelled = deepcopy(rows)
        cancelled[-1]["message"].pop("exitCode")
        self.assertEqual(pi.validate(encode(cancelled)), cancelled)

    def test_partial_tail_keeps_prefix_tracking_and_warning_until_complete(self):
        record, path, rows = self.fixture()
        tail = {"type": "message", "id": "tail", "parentId": rows[-1]["id"],
                "message": {"role": "user", "content": "Later question 汉"}}
        complete = encode(rows)
        pending = json.dumps(tail, ensure_ascii=False).encode()
        # Exercise both an ordinary partial JSON write and a split UTF-8 codepoint.
        for suffix in (b'{"type":', pending[:pending.index("汉".encode()) + 1]):
            with self.subTest(suffix=suffix):
                path.write_bytes(complete.encode() + suffix)
                self.track(record, count=5 if record.get("tracked") else 0)
                record["tracked"] = True
                result = self.sessions.get(self.pid, record["id"])["session"]
                self.assertEqual(result["native_sync_warning"]["code"], "incomplete_transcript")
                self.assertEqual(len(result["messages"]), 5)
                self.assertEqual(path.read_bytes(), complete.encode() + suffix)
                with self.assertRaises((SessionError, UnicodeDecodeError)):
                    pi.transcript(path.parent.parent, record["native_id"], self.store.root(self.pid))
                with patch.object(cli, "executable", return_value="fake-pi"), \
                        patch("easyread.workspaces.sessions.subprocess.Popen", side_effect=AssertionError("No launch")):
                    with self.assertRaises(SessionError) as error:
                        self.sessions.send(self.pid, record["id"], {"message": "Do not fork"})
                    self.assertEqual(error.exception.code, "reconciliation_required")
                self.assertEqual(self.sessions.cancel(self.pid, record["id"])["session"]["native_sync_warning"], result["native_sync_warning"])
        path.write_bytes(complete.encode() + pending)  # Complete JSON need not end in a newline.
        result = self.sessions.get(self.pid, record["id"])["session"]
        self.assertNotIn("native_sync_warning", result)
        self.assertEqual(len(result["messages"]), 6)
        self.assertEqual(result["messages"][-1]["content"], "Later question 汉")
        self.assertEqual(len(pi.messages(path.read_text())), 6)
        self.assertEqual(len(self.sessions.get(self.pid, record["id"])["session"]["messages"]), 6)

    def test_corrupt_session_isolated_and_repaired_without_erasing_prior_errors(self):
        bad, bad_path, rows = self.fixture()
        good, good_path, _ = self.fixture()
        self.track(bad)
        self.track(good)
        self.listing()
        self.sessions._commit(self.pid, lambda data: data["sessions"][bad["id"]].update(
            error="Existing provider failure", state="error", reconciliation_required=True))
        self.sessions._commit(self.pid, lambda data: [data["sessions"][r["id"]].update(
            terminal_tracking={"baseline": [], "message_count": 5}) for r in (bad, good)])
        broken = encode(rows) + '{"type":"message","id":"broken","parentId":"entry12","message":{"role":"assistant"}}\n'
        bad_path.write_text(broken)
        listed = {r["id"]: r for r in self.listing()}
        self.assertEqual(listed[bad["id"]]["native_sync_warning"]["code"], "invalid_transcript")
        self.assertNotIn("native_sync_warning", listed[good["id"]])
        self.assertEqual(listed[bad["id"]]["error"], "Existing provider failure")
        self.assertEqual(len(self.sessions.history(self.pid, bad["id"])["messages"]), 5)
        self.assertIn("native_sync_warning", self.sessions.updates(self.pid, bad["id"])["session"])
        with self.assertRaises(SessionError):
            self.sessions.terminal(self.pid, bad["id"])
        with patch.object(term, "unavailable_reason", return_value=""), patch.object(cli, "executable", return_value="fake-pi"):
            self.sessions.terminal(self.pid, good["id"])
        args = self.sessions.terminals.calls[-1]["argv"]
        self.assertEqual(args[args.index("--session") + 1], str(good_path))
        self.sessions.terminals.close_scope(self.pid, good["id"])
        # Missing/replaced files cannot clear the warning or invent a resume ID.
        bad_path.unlink()
        result = self.sessions.get(self.pid, bad["id"])["session"]
        self.assertEqual(result["native_sync_warning"]["code"], "not_found")
        self.assertEqual(result["native_id"], bad["native_id"])
        bad_path.write_text(encode(rows))
        result = self.sessions.get(self.pid, bad["id"])["session"]
        self.assertNotIn("native_sync_warning", result)
        self.assertEqual((result["state"], result["error"], result["reconciliation_required"]),
                         ("error", "Existing provider failure", True))
        self.assertEqual(len(result["messages"]), 5)

    def test_live_terminal_partial_tail_warning_is_durable_and_existing_terminal_remains_usable(self):
        record, path, rows = self.fixture()
        self.track(record)
        self.sessions.get(self.pid, record["id"])
        with patch.object(term, "unavailable_reason", return_value=""), patch.object(cli, "executable", return_value="fake-pi"):
            original_terminal = self.sessions.terminal(self.pid, record["id"])
        path.write_text(encode(rows) + "{")
        result = self.sessions.get(self.pid, record["id"])["session"]
        self.assertEqual(result["native_sync_warning"]["code"], "incomplete_transcript")
        cursor = result["cursor"]
        self.assertEqual(self.sessions.get(self.pid, record["id"])["session"]["cursor"], cursor)
        self.assertEqual(self.sessions.terminal(self.pid, record["id"])["url"], original_terminal["url"])
        self.assertEqual(len(self.sessions.terminals.calls), 1)
        restarted = Sessions(self.store)
        restarted.terminals = self.sessions.terminals
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.get(self.pid, record["id"])["session"]["native_sync_warning"], result["native_sync_warning"])
        path.write_text(encode(rows))
        self.assertNotIn("native_sync_warning", restarted.get(self.pid, record["id"])["session"])
        self.assertIn("terminal_tracking", restarted._load(self.pid)["sessions"][record["id"]])
        restarted.cancel(self.pid, record["id"])
        self.assertNotIn("terminal_tracking", restarted._load(self.pid)["sessions"][record["id"]])
        self.assertEqual(path.read_text(), encode(rows))

    def test_unknown_identity_and_truncated_history_do_not_guess_or_erase_saved_history(self):
        record, path, rows = self.fixture()
        self.track(record)
        self.sessions.get(self.pid, record["id"])
        self.track(record, count=5)
        path.write_text(encode(rows[:6]))
        result = self.sessions.get(self.pid, record["id"])["session"]
        self.assertEqual(result["native_sync_warning"]["code"], "native_conflict")
        self.assertEqual(len(result["messages"]), 5)
        path.write_text(encode(rows))
        self.sessions.get(self.pid, record["id"])
        unknown = self.create()
        # Discovery may not choose a valid candidate while another candidate is unreadable.
        corrupt = path.with_name("other_" + cli.new_native_id("pi") + ".jsonl")
        corrupt.write_text("not-json\n")
        self.sessions._commit(self.pid, lambda data: data["sessions"][unknown["id"]].update(
            native_id="", terminal_tracking={"baseline": [], "message_count": 0}))
        result = self.sessions.get(self.pid, unknown["id"])["session"]
        self.assertEqual(result["native_id"], "")
        self.assertIn("native_sync_warning", result)
        self.assertEqual(result["messages"], [])
        self.assertEqual(path.read_text(), encode(rows))
        self.assertEqual(corrupt.read_text(), "not-json\n")

    def unidentified_neighbor(self, active=False):
        healthy, path, _ = self.fixture()
        unknown = self.create()
        corrupt = path.with_name("unknown_" + cli.new_native_id("pi") + ".jsonl")
        corrupt.write_bytes(b"not-json\n")
        self.sessions._commit(self.pid, lambda data: data["sessions"][unknown["id"]].update(
            native_id="", terminal_tracking={"baseline": [healthy["native_id"]], "message_count": 0},
            messages=[{"role": "user", "content": "Saved before reconciliation", "at": cli.now_iso()}],
            state="error", error="Existing failure", reconciliation_required=True))
        if active:
            self.sessions.terminals.active[(self.pid, unknown["id"])] = "/workspace-terminal/unknown/"
        unknown = self.sessions.get(self.pid, unknown["id"])["session"]
        self.assertEqual(unknown["native_sync_warning"]["code"], "invalid_transcript")
        self.assertEqual(unknown["native_id"], "")
        return healthy, path, unknown, corrupt

    def test_inactive_unidentified_recovery_does_not_block_healthy_send_or_terminal(self):
        healthy, path, unknown, corrupt = self.unidentified_neighbor()
        self.assertFalse(self.sessions.terminals.scope_active(self.pid, unknown["id"]))
        script = self.base / "fake_pi.py"
        script.write_text(pi_fixtures.FAKE_PI)
        popen, calls = subprocess.Popen, []
        def start(args, **kwargs):
            calls.append(args)
            return popen([sys.executable, str(script), *args[1:]], **kwargs)
        with patch.object(cli, "executable", return_value="fake-pi"), \
                patch("easyread.workspaces.sessions.subprocess.Popen", side_effect=start):
            self.sessions.send(self.pid, healthy["id"], {"message": "Continue healthy session"})
            self.wait_idle()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][calls[0].index("--session") + 1], str(path))
        self.assertNotIn("--session-id", calls[0])
        self.assertEqual(self.sessions.get(self.pid, healthy["id"])["session"]["state"], "idle")
        with patch.object(term, "unavailable_reason", return_value=""), patch.object(cli, "executable", return_value="fake-pi"):
            self.sessions.terminal(self.pid, healthy["id"])
        args = self.sessions.terminals.calls[-1]["argv"]
        self.assertEqual(args[args.index("--session") + 1], str(path))
        self.sessions.terminals.close_scope(self.pid, healthy["id"])
        # Only the unrelated conversation is admitted; the unresolved one stays blocked.
        for operation in (lambda: self.sessions.send(self.pid, unknown["id"], {"message": "Do not replace"}),
                          lambda: self.sessions.terminal(self.pid, unknown["id"])):
            with self.assertRaises(SessionError) as error:
                operation()
            self.assertEqual((error.exception.code, error.exception.status), ("reconciliation_required", 409))
        retained = self.sessions.cancel(self.pid, unknown["id"])["session"]
        for key in ("native_id", "native_sync_warning", "messages", "state", "error", "reconciliation_required"):
            self.assertEqual(retained[key], unknown[key])
        self.assertIn("terminal_tracking", self.sessions._load(self.pid)["sessions"][unknown["id"]])
        self.assertEqual(corrupt.read_bytes(), b"not-json\n")

    def test_active_unidentified_writer_still_blocks_healthy_send_and_terminal(self):
        healthy, path, unknown, corrupt = self.unidentified_neighbor(active=True)
        original = path.read_bytes()
        with patch.object(term, "unavailable_reason", return_value=""), patch.object(cli, "executable", return_value="fake-pi"), \
                patch("easyread.workspaces.sessions.subprocess.Popen", side_effect=AssertionError("No launch")):
            for operation in (lambda: self.sessions.send(self.pid, healthy["id"], {"message": "Wait for writer"}),
                              lambda: self.sessions.terminal(self.pid, healthy["id"])):
                with self.assertRaises(SessionError) as error:
                    operation()
                self.assertEqual((error.exception.code, error.exception.status), ("conflict", 409))
                self.assertIn("record its session ID", str(error.exception))
        self.assertEqual(self.sessions.terminals.calls, [])
        self.assertTrue(self.sessions.terminals.scope_active(self.pid, unknown["id"]))
        self.assertEqual(self.sessions.get(self.pid, healthy["id"])["session"]["messages"], [])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(corrupt.read_bytes(), b"not-json\n")
        retained = self.sessions.cancel(self.pid, unknown["id"])["session"]
        self.assertFalse(self.sessions.terminals.scope_active(self.pid, unknown["id"]))
        self.assertEqual(retained["native_sync_warning"], unknown["native_sync_warning"])
        self.assertEqual(retained["native_id"], "")
        self.assertEqual(retained["messages"], unknown["messages"])
        with patch.object(term, "unavailable_reason", return_value=""), patch.object(cli, "executable", return_value="fake-pi"):
            self.sessions.terminal(self.pid, healthy["id"])
        args = self.sessions.terminals.calls[-1]["argv"]
        self.assertEqual(args[args.index("--session") + 1], str(path))

    def test_strict_import_rejects_partial_and_malformed_records_transactionally(self):
        self.fixture()
        destination = self.base / "strict-export"
        self.sessions.export_state(self.pid, destination)
        path = next((destination / "native" / "pi" / "sessions").glob("*.jsonl"))
        original = path.read_text()
        manifest_path = destination / "manifest.json"
        for suffix in ("{", '{"type":"message","id":"bad","parentId":"entry12","message":{"role":"user"}}\n'):
            with self.subTest(suffix=suffix):
                path.write_text(original + suffix)
                manifest = json.loads(manifest_path.read_text())
                manifest["entries"] = [e for e in session_archive._inventory(destination) if e["path"] != "manifest.json"]
                manifest_path.write_text(json.dumps(manifest))
                pid = self.store.allocate({"name": "Reject malformed native import"})["id"]
                with self.assertRaises(SessionError):
                    self.sessions.import_state(pid, destination)
                self.assertEqual(self.sessions._load(pid)["order"], [])
                self.assertFalse(cli.project_home(self.store.meta(pid), "pi").exists())

    def test_identity_cwd_tree_filename_and_link_safeguards_remain_strict(self):
        record, path, rows = self.fixture()
        for mutate in (lambda r: r[0].update(id=cli.new_native_id("pi")),
                       lambda r: r[1].update(parentId="missing"),
                       lambda r: r[-1].update(id=r[1]["id"])):
            changed = deepcopy(rows)
            mutate(changed)
            path.write_text(encode(changed))
            with self.assertRaises(SessionError):
                pi.transcript(path.parent.parent, record["native_id"], self.store.root(self.pid))
        path.write_text(encode(rows))
        self.assertIsNone(pi.transcript(path.parent.parent, record["native_id"], self.base))
        duplicate = path.with_name("duplicate_" + record["native_id"] + ".jsonl")
        duplicate.write_bytes(path.read_bytes())
        with self.assertRaises(SessionError):
            pi.transcript(path.parent.parent, record["native_id"], self.store.root(self.pid))
        duplicate.unlink()
        renamed = path.with_name("invalid-name.jsonl")
        path.rename(renamed)
        with self.assertRaises(SessionError):
            pi.project_transcripts(path.parent.parent, self.store.root(self.pid))
        path.symlink_to(renamed)
        with self.assertRaises(ValueError):
            pi.transcript(path.parent.parent, record["native_id"], self.store.root(self.pid))


if __name__ == "__main__":
    unittest.main()

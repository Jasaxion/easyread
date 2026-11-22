"""Persistent Pi workspace RPC lifecycle and input safety, using a local peer."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from easyread.workspaces import pi_runtime, pi_sessions, session_cli as cli, session_terminal as term
from easyread.workspaces.sessions import SessionError, Sessions
from tests import test_workspace_sessions as fixtures
from tests.test_qa_images import picture

FAKE_PI = Path(__file__).with_name("pi_rpc_peer.py").read_text()


class PiRPCTest(unittest.TestCase):
    setUpBase = fixtures.WorkspaceSessionsTest.setUp
    native = fixtures.WorkspaceSessionsTest.native
    wait_idle = fixtures.WorkspaceSessionsTest.wait_idle

    def setUp(self):
        self.setUpBase()
        self.script = self.base / "pi-peer"
        self.script.write_text("#!" + sys.executable + "\n" + FAKE_PI)
        self.script.chmod(0o700)
        self.override = patch.dict(os.environ, {"EASYREAD_WORKSPACE_PI_COMMAND": str(self.script)})
        self.override.start()
        self.addCleanup(self.override.stop)
        self.record = self.sessions.create(self.pid, {"engine": "pi"})["session"]
        self.sid = self.record["id"]

    def get(self):
        return self.sessions.get(self.pid, self.sid)["session"]

    def runtime(self):
        return self.sessions.native.runtimes[(self.pid, self.sid)]

    def wait(self, predicate, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = self.get()
            if predicate(value):
                return value
            time.sleep(.01)
        self.fail("Pi condition not reached: " + json.dumps(self.get(), ensure_ascii=False))

    def receipt(self, result, status):
        rid = result["submission"]["id"]
        return self.wait(lambda r: any(item["id"] == rid and item["status"] == status for item in r.get("pending_inputs", [])))

    def send(self, text="hold", **kwargs):
        result = self.sessions.send(self.pid, self.sid, {"message": text, **kwargs})
        self.assertEqual(result["submission"]["status"], "submitting")
        return result

    def stable(self):
        self.wait(lambda r: not self.runtime().identity_checks and not self.runtime().submission_pending)

    def test_alive_after_settled_and_two_turns_keep_one_native_process(self):
        self.send("first\u2028question")
        self.wait_idle()
        runtime = self.runtime()
        process = runtime.rpc.proc
        self.assertIsNone(process.poll())
        self.assertEqual(self.get()["state"], "idle")
        self.assertEqual(self.get()["runtime_state"], "awake")
        self.send("second")
        self.wait_idle()
        self.assertIs(self.runtime().rpc.proc, process)
        self.assertEqual([m["content"] for m in self.get()["messages"]], ["first\u2028question", "answer 1", "second", "answer 2"])
        self.assertNotIn("secret-thinking", json.dumps(self.get()))
        self.sessions.prepare_export(self.pid)
        self.assertIsNotNone(process.poll())
        self.assertFalse(self.sessions.native.loaded(self.pid))
        self.send("third")
        self.wait_idle()
        self.assertEqual(self.get()["native_id"], self.record["native_id"])
        self.assertEqual(self.get()["messages"][-1]["content"], "answer 3")

    def test_agent_end_retry_compaction_and_metadata_idle_do_not_finish(self):
        from easyread.server.task_notices import Notices
        notices = Notices(self.store.libroot)
        self.sessions.observer = notices.observe
        self.send()
        self.stable()
        runtime = self.runtime()
        for event, phase in (({"type": "agent_end", "willRetry": True}, "retrying"),
                             ({"type": "auto_retry_start", "attempt": 1}, "retrying"),
                             ({"type": "compaction_start", "reason": "overflow"}, "compacting"),
                             ({"type": "compaction_end", "willRetry": True}, "settling")):
            runtime.rpc.call("fixture_event", {"event": event})
            self.assertEqual(self.get()["runtime_phase"], phase)
            self.assertEqual(notices.snapshot()[0]["state"], "running")
            self.assertTrue(self.sessions.busy(self.pid))
            self.assertFalse(runtime.rpc.call("get_state")["isStreaming"])
        runtime.last_activity -= 1000
        self.sessions.native.reap()
        self.assertIsNone(runtime.rpc.proc.poll())
        runtime.rpc.call("fixture_finish")
        self.wait_idle()
        self.assertEqual(self.get()["state"], "idle")
        self.assertEqual(notices.snapshot()[0]["state"], "completed")
        self.assertEqual(len(notices.snapshot()), 1)

    def test_queue_duplicates_skills_images_references_and_delivery_without_duplicates(self):
        self.send()
        self.stable()
        (self.store.root(self.pid) / "paper.md").write_text("local reference")
        runtime = self.runtime()
        for kind, text in (("steer", "same"), ("steer", "same"), ("follow_up", "$review check")):
            body = {"message": text, "images": [picture()], "files": ["paper.md"],
                    "expected_turn_id": runtime.active_turn, "generation": runtime.generation}
            result = getattr(self.sessions, kind)(self.pid, self.sid, body)
            self.receipt(result, "queued")
            self.stable()
        public = self.get()
        self.assertEqual(public["native_queue"], {"steering": 2, "follow_up": 1})
        self.assertNotIn("SECRET SKILL BODY", json.dumps(public))
        runtime.rpc.call("fixture_finish")
        self.wait_idle()
        public = self.get()
        self.assertEqual([m["content"] for m in public["messages"] if m["role"] == "user"], ["hold", "same", "same", "$review check"])
        self.assertTrue(all(i["status"] == "delivered" for i in public["pending_inputs"]))
        self.assertTrue(all((self.store.root(self.pid) / i["images"][0]).is_file() for i in public["pending_inputs"][1:]))
        self.assertEqual(public["pending_input_count"], 0)
        self.assertEqual(public["native_queue"], {"steering": 0, "follow_up": 0})
        rows = pi_sessions.read_snapshot(runtime.session_file).rows
        self.assertTrue(any(block.get("type") == "image" for row in rows if row.get("message", {}).get("role") == "user"
                            for block in row["message"]["content"]))

    def test_clear_queue_and_stop_clear_before_abort(self):
        self.send()
        self.stable()
        result = self.sessions.follow_up(self.pid, self.sid, {"message": "queued"})
        self.receipt(result, "queued")
        self.stable()
        self.sessions.clear_queue(self.pid, self.sid)
        self.assertEqual(self.get()["pending_inputs"][-1]["status"], "cleared")
        result = self.sessions.steer(self.pid, self.sid, {"message": "never run"})
        self.receipt(result, "queued")
        self.stable()
        process = self.runtime().rpc.proc
        self.sessions.cancel(self.pid, self.sid)
        self.assertIsNotNone(process.poll())
        records = [json.loads(line) for line in (self.store.root(self.pid) / "pi-rpc-commands.jsonl").read_text().splitlines()]
        types = [r["type"] for r in records]
        self.assertLess(max(i for i, v in enumerate(types) if v == "clear_queue"), types.index("abort"))
        self.assertFalse(self.sessions.busy(self.pid))
        self.assertEqual(self.get()["pending_inputs"][-1]["status"], "cleared")

    def test_dialog_before_prompt_ack_is_answerable_and_generation_scoped(self):
        for method in ("select", "confirm", "input", "editor"):
            result = self.send("/dialog " + method)
            record = self.wait(lambda r: bool(r["pending_requests"]))
            request = record["pending_requests"][0]
            self.assertEqual(record["state"], "waiting_user")
            self.assertEqual(record["pending_inputs"][-1]["status"], "submitting")
            self.assertTrue(record["submission_pending"])
            with self.assertRaises(SessionError) as error:
                self.sessions.respond(self.pid, self.sid, {"request_id": request["id"], "generation": "stale", "cancelled": True})
            self.assertEqual(error.exception.code, "request_expired")
            body = {"request_id": request["id"], "generation": request["generation"]}
            body.update({"confirmed": False} if method == "confirm" else {"value": "one" if method == "select" else "edited"})
            self.sessions.respond(self.pid, self.sid, body)
            self.receipt(result, "handled")
            self.wait_idle()
            with self.assertRaises(SessionError):
                self.sessions.respond(self.pid, self.sid, body)
        self.send("/dialog confirm")
        self.wait(lambda r: bool(r["pending_requests"]))
        self.sessions.cancel(self.pid, self.sid)
        self.assertFalse(self.sessions.busy(self.pid))

    def test_rejection_transport_loss_and_restart_preserve_attachments_without_replay(self):
        result = self.send("reject", images=[picture()])
        record = self.receipt(result, "failed")
        self.stable()
        self.assertTrue((self.store.root(self.pid) / record["pending_inputs"][-1]["images"][0]).is_file())
        result = self.send("disconnect", images=[picture()])
        record = self.receipt(result, "unknown")
        self.assertTrue(record["reconciliation_required"])
        self.assertEqual(record["state"], "interrupted")
        with self.assertRaises(SessionError):
            self.send("must not silently restart")
        self.sessions.close()
        restarted = Sessions(self.store)
        self.addCleanup(restarted.close)
        record = restarted.get(self.pid, self.sid)["session"]
        self.assertTrue(record["reconciliation_required"])
        self.assertEqual(record["pending_inputs"][-1]["status"], "unknown")
        self.assertFalse(restarted.native.loaded())
        restarted.cancel(self.pid, self.sid)
        restarted.send(self.pid, self.sid, {"message": "explicit new input"})
        end = time.monotonic() + 5
        while restarted.busy() and time.monotonic() < end:
            time.sleep(.01)
        self.assertFalse(restarted.busy())
        self.assertEqual(restarted.get(self.pid, self.sid)["session"]["pending_inputs"][-2]["status"], "unknown")

    def test_late_ack_does_not_remove_unknown_gate_or_replay(self):
        self.send("/late")
        self.wait(lambda r: bool(self.runtime().fenced))
        runtime = self.runtime()
        runtime.submitted_at -= 100
        runtime.maintain()
        self.assertEqual(self.get()["pending_inputs"][-1]["status"], "unknown")
        runtime.rpc.call("fixture_late")
        self.wait(lambda r: r["pending_inputs"][-1].get("late_disposition") == "handled")
        self.assertTrue(self.get()["reconciliation_required"])
        self.assertEqual(self.get()["pending_inputs"][-1]["status"], "unknown")
        runtime.last_activity -= 1000
        self.sessions.native.reap()
        self.assertIsNone(runtime.rpc.proc.poll())

    def test_follow_up_continues_same_session_if_settlement_wins_the_race(self):
        self.send()
        self.stable()
        runtime = self.runtime()
        observed_turn, generation = runtime.active_turn, runtime.generation
        runtime.rpc.call("fixture_finish")
        self.wait_idle()
        result = self.sessions.follow_up(self.pid, self.sid, {
            "message": "continue after settling", "expected_turn_id": observed_turn, "generation": generation})
        self.receipt(result, "delivered")
        self.wait_idle()
        self.assertIs(self.runtime(), runtime)
        self.assertEqual(self.get()["native_id"], self.record["native_id"])
        self.assertEqual([m["content"] for m in self.get()["messages"] if m["role"] == "user"],
                         ["hold", "continue after settling"])
        self.assertFalse(self.get()["submission_pending"])
        self.assertEqual(self.get()["pending_input_count"], 0)
        with self.assertRaises(SessionError) as error:
            self.sessions.follow_up(self.pid, self.sid, {"message": "stale runtime", "generation": "old"})
        self.assertEqual(error.exception.code, "stale_generation")
        self.assertFalse(self.runtime().uncertain)
        self.assertEqual(self.get()["state"], "idle")

    def test_retired_unknown_receipts_do_not_exhaust_capacity_or_lose_journal_evidence(self):
        from easyread.workspaces import pi_controls, pi_inputs
        identities = []
        with patch.object(pi_inputs, "MAX_INPUTS", 3), patch.object(pi_controls, "MAX_INPUTS", 3):
            for _ in range(6):
                result = self.send("/late")
                identities.append(result["submission"]["id"])
                self.wait(lambda r: self.runtime().fenced)
                runtime = self.runtime()
                runtime.submitted_at -= 100
                runtime.maintain()
                record = self.sessions.cancel(self.pid, self.sid)["session"]
                self.assertIsNotNone(runtime.rpc.proc.poll())
                self.assertTrue(record["pending_inputs"][-1]["retired"])
                self.assertEqual(record["pending_inputs"][-1]["status"], "unknown")
                self.assertEqual(record["pending_input_count"], 0)
                self.assertFalse(record["submission_pending"])
                self.assertFalse(record["reconciliation_required"])
        self.assertLessEqual(len(self.get()["pending_inputs"]), 3)
        for rid in identities:
            message = self.sessions._journal(self.pid).message(self.sid, rid)
            self.assertEqual(message["delivery_status"], "unknown")
            self.assertTrue(message["delivery_retired"])
            self.assertTrue(message["delivery_error"])
            self.assertEqual(message["content"], "/late")
        self.send("fresh explicit input")
        self.wait_idle()

    def test_unowned_crash_generation_stays_blocked_without_affecting_healthy_sessions(self):
        def crash(data):
            record = data["sessions"][self.sid]
            record.update(generation="unowned-crash", runtime_state="awake", state="running", submission_pending=True,
                          pending_inputs=[{"id": "lost-input", "kind": "send", "message": "do not replay", "status": "submitting"}])
            record["messages"].append({"id": "lost-input", "role": "user", "content": "do not replay", "delivery_status": "submitting"})
        self.sessions._commit(self.pid, crash)
        recovered = Sessions(self.store)
        self.addCleanup(recovered.close)
        record = recovered.get(self.pid, self.sid)["session"]
        self.assertEqual(record["pending_inputs"][0]["status"], "unknown")
        self.assertFalse(record["submission_pending"])
        for operation in (lambda: recovered.cancel(self.pid, self.sid),
                          lambda: recovered.terminal(self.pid, self.sid),
                          lambda: recovered.settings(self.pid, self.sid, {})):
            with self.assertRaises(SessionError) as error:
                operation()
            self.assertEqual(error.exception.code, "reconciliation_required")
        retained = recovered.get(self.pid, self.sid)["session"]
        self.assertFalse(retained["pending_inputs"][0].get("retired"))
        self.assertEqual(retained["messages"][0]["content"], "do not replay")
        healthy = recovered.create(self.pid, {"engine": "pi"})["session"]
        recovered.send(self.pid, healthy["id"], {"message": "healthy"})
        end = time.monotonic() + 5
        while recovered.busy() and time.monotonic() < end:
            time.sleep(.01)
        self.assertFalse(recovered.busy())
        self.assertEqual(recovered.get(self.pid, healthy["id"])["session"]["state"], "idle")

    def test_commands_cached_goal_native_unknown_skill_literal_and_identity_fenced(self):
        self.assertFalse(self.sessions.native.loaded())
        with patch.object(self.sessions, "capabilities", return_value={"engines": {e: {} for e in cli.ENGINES}}), \
             patch.object(cli, "engine_status", side_effect=lambda e, t: {"id": e, "capabilities": {}}):
            self.sessions.listing(self.pid)
        self.assertFalse(self.sessions.native.loaded())
        first = self.sessions.commands(self.pid, self.sid)
        self.assertEqual(first, self.sessions.commands(self.pid, self.sid))
        self.assertNotIn("/private", json.dumps(first))
        self.send("/goal set native extension")
        self.wait_idle()
        self.assertEqual(self.get()["pending_inputs"][-1]["status"], "handled")
        result = self.send("/new")
        self.receipt(result, "failed")
        self.assertEqual(self.get()["native_id"], self.record["native_id"])
        self.send("$HOME is ordinary text")
        self.wait_idle()
        result = self.send("/identity-change")
        self.wait(lambda r: r.get("reconciliation_required"))
        self.assertEqual(self.get()["native_id"], self.record["native_id"])
        self.assertNotIn("WRONG SESSION", json.dumps(self.get()))

    def test_idle_terminal_handoff_capacity_and_stale_turn(self):
        self.sessions.native.max_active = 1
        self.send()
        self.stable()
        second = self.sessions.create(self.pid, {"engine": "pi"})["session"]
        with self.assertRaises(SessionError) as error:
            self.sessions.send(self.pid, second["id"], {"message": "blocked"})
        self.assertEqual(error.exception.code, "active_capacity")
        with self.assertRaises(SessionError) as error:
            self.sessions.steer(self.pid, self.sid, {"message": "stale", "expected_turn_id": "old-run"})
        self.assertEqual(error.exception.code, "stale_turn")
        self.runtime().rpc.call("fixture_finish")
        self.wait_idle()
        process = self.runtime().rpc.proc
        with patch.object(term, "unavailable_reason", return_value=""):
            result = self.sessions.terminal(self.pid, self.sid)
        self.assertIsNotNone(process.poll())
        self.assertEqual(result["native_id"], self.record["native_id"])
        self.assertIn("--session", self.sessions.terminals.calls[-1]["argv"])
        with self.assertRaises(SessionError):
            self.send("exclusive writer")

    def test_compact_notice_timeout_and_malformed_stream_are_bounded(self):
        self.send("done")
        self.wait_idle()
        result = self.sessions.command(self.pid, self.sid, {"command": "compact", "arguments": "focus"})
        self.receipt(result, "handled")
        self.wait_idle()
        self.send("/notice")
        self.wait_idle()
        self.assertTrue(any("draft was not changed" in e.get("text", "") for e in self.get()["events"]))
        runtime = self.runtime()
        runtime.rpc.call("fixture_event", {"event": {"type": "extension_ui_request", "id": "expires", "method": "input", "timeout": 1}})
        time.sleep(.01)
        runtime.maintain()
        with self.assertRaises(SessionError) as error:
            self.sessions.respond(self.pid, self.sid, {"request_id": "expires", "generation": runtime.generation, "value": "late"})
        self.assertEqual(error.exception.code, "request_expired")
        self.send("malformed")
        self.wait(lambda r: r.get("reconciliation_required"))
        healthy = self.sessions.create(self.pid, {"engine": "pi"})["session"]
        self.assertEqual(self.sessions.get(self.pid, healthy["id"])["session"]["state"], "idle")


if __name__ == "__main__":
    unittest.main()

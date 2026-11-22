"""Pi display groups follow native delivery, independently of the steering guard."""
import json
import time
import unittest

from easyread.workspaces.sessions import Sessions
from tests import test_workspace_pi_rpc as rpc_fixtures


class PiDisplayGroupingTest(unittest.TestCase):
    setUpBase = rpc_fixtures.PiRPCTest.setUpBase
    setUp = rpc_fixtures.PiRPCTest.setUp
    get = rpc_fixtures.PiRPCTest.get
    runtime = rpc_fixtures.PiRPCTest.runtime
    wait = rpc_fixtures.PiRPCTest.wait
    receipt = rpc_fixtures.PiRPCTest.receipt
    send = rpc_fixtures.PiRPCTest.send
    stable = rpc_fixtures.PiRPCTest.stable
    wait_idle = rpc_fixtures.PiRPCTest.wait_idle

    def event(self, kind, **fields):
        self.runtime().rpc.call("fixture_event", {"event": {"type": kind, **fields}})

    def queued_continuation(self, text="$review next"):
        first = self.send()["submission"]["id"]
        self.stable()
        run = self.get()["active_turn_id"]
        result = self.sessions.follow_up(self.pid, self.sid, {"message": text})
        record = self.receipt(result, "queued")
        self.stable()
        second = result["submission"]["id"]
        self.assertEqual([item["turn_id"] for item in record["pending_inputs"]], [first, second])
        self.assertEqual([m["turn_id"] for m in record["messages"] if m["role"] == "user"], [first, second])
        self.assertEqual(record["active_display_turn_id"], first)
        self.assertEqual(record["active_turn_id"], run)
        return first, second, run

    def groups(self, record):
        # The renderer uses first-seen turn_id order, not native process/run IDs.
        groups = {}
        for message in record["messages"]:
            groups.setdefault(message["turn_id"], []).append(message["content"])
        return list(groups.items())

    def test_native_delivery_groups_both_answers_before_settlement(self):
        first, second, run = self.queued_continuation()
        self.runtime().rpc.call("fixture_deliver")
        record = self.get()
        self.assertEqual(self.groups(record), [(first, ["hold", "answer 1"]),
                                               (second, ["$review next", "answer 2"])])
        self.assertEqual(record["active_display_turn_id"], second)
        self.assertEqual(record["active_turn_id"], run)
        self.assertEqual(record["state"], "running")
        self.assertNotIn("SECRET SKILL BODY", json.dumps(record))
        self.runtime().rpc.call("fixture_finish")
        self.wait_idle()
        self.assertIsNone(self.get()["active_display_turn_id"])

    def test_queued_user_cannot_take_tools_or_an_assistant_started_before_delivery(self):
        first, second, run = self.queued_continuation("next")
        # Make deferred flushing deterministic: only this test drives it.
        self.sessions.native.stop_event.set()
        self.sessions.native.reaper.join(5)
        self.event("tool_execution_start", toolCallId="read-1", toolName="read", args={"path": "paper.md"})
        queued = self.get()
        self.assertEqual(queued["active_display_turn_id"], first)
        self.assertEqual(queued["events"][-1]["turn_id"], first)
        self.assertEqual(queued["pending_inputs"][-1]["status"], "queued")
        self.event("message_start", message={"role": "assistant"})
        self.runtime().last_flush = time.monotonic() + 60
        self.event("message_update", assistantMessageEvent={"type": "text_delta", "contentIndex": 0, "delta": "late first answer"})
        self.runtime().rpc.call("fixture_deliver", {"answer": False})
        self.assertEqual(self.get()["active_display_turn_id"], second)
        self.event("message_end", message={"role": "assistant", "content": [{"type": "text", "text": "late first answer"}], "stopReason": "stop"})
        self.event("tool_execution_end", toolCallId="read-1", toolName="read", result={"content": [{"type": "text", "text": "read complete"}]}, isError=False)
        self.event("message_start", message={"role": "assistant"})
        self.event("message_end", message={"role": "assistant", "content": [{"type": "text", "text": "second answer"}], "stopReason": "stop"})
        record = self.get()
        self.assertEqual(self.groups(record), [(first, ["hold", "answer 1", "late first answer"]),
                                               (second, ["next", "second answer"])])
        self.assertEqual([(event["kind"], event["turn_id"]) for event in record["events"]],
                         [("tool", first), ("tool_result", first)])
        self.assertEqual(record["active_turn_id"], run)
        self.runtime().rpc.call("fixture_finish")
        self.wait_idle()

    def test_low_level_restarts_keep_run_guard_and_paging_preserves_display_ids(self):
        first, second, run = self.queued_continuation()
        self.event("agent_end", willRetry=True)
        self.event("agent_start")
        self.assertEqual((self.get()["active_turn_id"], self.get()["active_display_turn_id"]), (run, first))
        self.runtime().rpc.call("fixture_deliver")
        self.event("agent_end", willRetry=True)
        self.event("agent_start")
        self.assertEqual((self.get()["active_turn_id"], self.get()["active_display_turn_id"]), (run, second))
        self.event("tool_execution_start", toolCallId="read-2", toolName="read", args={"path": "paper.md"})
        self.event("tool_execution_end", toolCallId="read-2", toolName="read", result={"content": []}, isError=False)
        self.runtime().rpc.call("fixture_finish")
        self.wait_idle()
        self.sessions.prepare_export(self.pid)
        reopened = Sessions(self.store)
        self.addCleanup(reopened.close)
        record = reopened.get(self.pid, self.sid)["session"]
        self.assertEqual(self.groups(record), [(first, ["hold", "answer 1"]),
                                               (second, ["$review next", "answer 2"])])
        self.assertEqual([e["turn_id"] for e in record["events"]], [second, second])
        self.assertIsNone(record["active_display_turn_id"])
        before, messages = None, {}
        while True:
            page = reopened.history(self.pid, self.sid, before, limit=1)
            self.assertNotIn("SECRET SKILL BODY", json.dumps(page))
            self.assertNotIn("/private/SKILL.md", json.dumps(page))
            messages.update((m["id"], m) for m in page["messages"])
            if not page["has_more"]:
                break
            before = page["history_before"]
        self.assertEqual({m["id"]: m["turn_id"] for m in record["messages"]},
                         {mid: m["turn_id"] for mid, m in messages.items()})


if __name__ == "__main__":
    unittest.main()

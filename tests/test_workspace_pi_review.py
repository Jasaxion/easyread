"""Focused regressions for Pi RPC primary review findings 1–4."""
import base64
import io
import json
import random
import time
import types
import unittest
from unittest.mock import patch

from easyread.workspaces import codex_protocol, pi_protocol, pi_requests, pi_runtime
from easyread.workspaces.sessions import Sessions, SessionError
from tests import test_workspace_pi_rpc as rpc_fixtures
from tests.test_qa_images import picture


class PiPrimaryReviewTest(unittest.TestCase):
    setUpBase = rpc_fixtures.PiRPCTest.setUpBase
    setUp = rpc_fixtures.PiRPCTest.setUp
    get = rpc_fixtures.PiRPCTest.get
    runtime = rpc_fixtures.PiRPCTest.runtime
    wait = rpc_fixtures.PiRPCTest.wait
    receipt = rpc_fixtures.PiRPCTest.receipt
    send = rpc_fixtures.PiRPCTest.send
    stable = rpc_fixtures.PiRPCTest.stable
    wait_idle = rpc_fixtures.PiRPCTest.wait_idle

    def test_writer_reservation_precedes_spawn_and_idle_crash_requires_confirmed_release(self):
        spawn = pi_protocol.PiRPC
        reservations = []
        def observed_spawn(*args, **kwargs):
            saved = self.sessions._journal(self.pid).load()["sessions"][self.sid]
            reservations.append(saved["generation"])
            self.assertEqual(saved["generation"], kwargs["generation"])
            self.assertEqual(saved["runtime_state"], "starting")
            self.assertNotEqual(saved.get("runtime_released_generation"), saved["generation"])
            return spawn(*args, **kwargs)
        with patch.object(pi_protocol, "PiRPC", side_effect=observed_spawn):
            self.send("done")
            self.wait_idle()
        process = self.runtime().rpc.proc
        self.assertEqual(self.get()["state"], "idle")
        self.assertEqual(self.get()["runtime_state"], "awake")
        recovered = Sessions(self.store)
        self.addCleanup(recovered.close)
        self.assertTrue(recovered.get(self.pid, self.sid)["session"]["reconciliation_required"])
        for action in (lambda: recovered.send(self.pid, self.sid, {"message": "duplicate writer"}),
                       lambda: recovered.cancel(self.pid, self.sid),
                       lambda: recovered.terminal(self.pid, self.sid),
                       lambda: recovered.settings(self.pid, self.sid, {})):
            with self.assertRaises(SessionError) as error:
                action()
            self.assertEqual(error.exception.code, "reconciliation_required")
        self.assertIsNone(process.poll())
        self.sessions.cancel(self.pid, self.sid)  # The actual owner can confirm exit.
        self.assertIsNotNone(process.poll())
        recovered.send(self.pid, self.sid, {"message": "explicit continuation"})
        end = time.monotonic() + 5
        while recovered.busy() and time.monotonic() < end:
            time.sleep(.01)
        self.assertFalse(recovered.busy())
        self.assertNotEqual(recovered.get(self.pid, self.sid)["session"]["generation"], reservations[0])
        failed = self.sessions.create(self.pid, {"engine": "pi"})["session"]
        with patch.object(codex_protocol.AppServer, "_spawn", side_effect=OSError("fixture exec failure")):
            with self.assertRaises(SessionError) as error:
                self.sessions.send(self.pid, failed["id"], {"message": "not started"})
        self.assertEqual(error.exception.code, "engine_unavailable")
        saved = self.sessions._journal(self.pid).load()["sessions"][failed["id"]]
        self.assertEqual(saved["generation"], saved["runtime_released_generation"])

    def test_identical_queue_shrink_delivers_oldest_and_clear_only_retires_newer(self):
        self.send()
        self.stable()
        results = []
        for _ in range(2):
            result = self.sessions.follow_up(self.pid, self.sid, {"message": "identical"})
            self.receipt(result, "queued")
            self.stable()
            results.append(result["submission"]["id"])
        runtime = self.runtime()
        self.assertEqual([entry["text"] for entry in runtime.inputs.queues["followUp"]], ["identical", "identical"])
        runtime.rpc.call("fixture_deliver")
        statuses = {item["id"]: item["status"] for item in self.get()["pending_inputs"]}
        self.assertEqual([statuses[rid] for rid in results], ["delivered", "queued"])
        self.assertEqual(self.get()["active_display_turn_id"], results[0])
        self.sessions.clear_queue(self.pid, self.sid)
        statuses = {item["id"]: item["status"] for item in self.get()["pending_inputs"]}
        self.assertEqual([statuses[rid] for rid in results], ["delivered", "cleared"])
        runtime.rpc.call("fixture_finish")
        self.wait_idle()

    def test_image_budget_and_definitely_unwritten_payload_do_not_poison_live_runtime(self):
        from PIL import Image
        raw = random.Random(0).randbytes(1536 * 1536 * 3)
        output = io.BytesIO()
        Image.frombytes("RGB", (1536, 1536), raw).save(output, "PNG")
        image = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
        self.assertLess(len(output.getvalue()), 8 * 1024 * 1024)
        self.assertGreater(len(image), 8 * 1024 * 1024)
        result = self.send("hold image", images=[image])
        self.receipt(result, "delivered")
        self.stable()
        runtime, process = self.runtime(), self.runtime().rpc.proc
        with patch.object(pi_protocol.PiRPC, "max_line", 4096):
            result = self.sessions.follow_up(self.pid, self.sid, {"message": "x" * 12000, "images": [picture()]})
            record = self.receipt(result, "failed")
            item = record["pending_inputs"][-1]
            self.assertEqual(item["error_code"], "payload_too_large")
            self.assertTrue((self.store.root(self.pid) / item["images"][0]).is_file())
            self.assertFalse(runtime.uncertain)
            self.assertFalse(record["reconciliation_required"])
            self.assertEqual(record["state"], "running")
            self.assertIsNone(process.poll())
            result = self.sessions.follow_up(self.pid, self.sid, {"message": "valid next input"})
            self.receipt(result, "queued")
            self.stable()
        commands = [json.loads(line) for line in (self.store.root(self.pid) / "pi-rpc-commands.jsonl").read_text().splitlines()]
        self.assertFalse(any(command.get("id") == item["id"] for command in commands))
        runtime.rpc.call("fixture_finish")
        self.wait_idle()

    def test_ack_deadline_excludes_dialog_and_native_settling_waits_then_resumes(self):
        self.send("/late")
        self.wait(lambda _: self.runtime().fenced)
        runtime = self.runtime()
        self.sessions.native.stop_event.set()
        self.sessions.native.reaper.join(5)
        clock = [time.monotonic()]
        fake_time = types.SimpleNamespace(monotonic=lambda: clock[0], time=time.time)
        def event(kind, **fields):
            runtime.rpc.call("fixture_event", {"event": {"type": kind, **fields}})
        with patch.object(pi_runtime, "time", fake_time), patch.object(pi_requests, "time", fake_time):
            event("extension_ui_request", id="human", method="input", title="Take your time")
            clock[0] += 120
            runtime.maintain()
            self.assertFalse(runtime.uncertain)
            self.sessions.respond(self.pid, self.sid, {"request_id": "human", "generation": runtime.generation, "value": "answer"})
            runtime.maintain()
            self.assertFalse(runtime.uncertain)
            clock[0] += 30
            event("agent_end", willRetry=False)  # Native can defer prompt during settling.
            clock[0] += 120
            runtime.maintain()
            self.assertFalse(runtime.uncertain)
            event("agent_settled", aborted=False)
            runtime.maintain()
            self.assertFalse(runtime.uncertain)
            event("extension_ui_request", id="timeout", method="input", timeout=10000)
            clock[0] += 11
            runtime.maintain()
            self.assertFalse(runtime.uncertain)
            self.assertNotIn("timeout", runtime.pending)
            clock[0] += pi_runtime.ACK_TIMEOUT + 1
            runtime.maintain()
            self.assertTrue(runtime.uncertain)
            self.assertEqual(self.get()["pending_inputs"][-1]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()

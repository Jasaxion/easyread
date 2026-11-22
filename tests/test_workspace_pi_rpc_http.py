"""Authenticated native Pi RPC routes locally and through a disposable HTTP worker."""
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

from tests import test_workspace_http as local, test_workspace_remote_http as remote
from tests.test_workspace_pi_rpc import FAKE_PI
from tests.test_qa_images import picture


class PiRPCRemoteHTTPTest(unittest.TestCase):
    setUp_local = local.WorkspaceHTTPTest.setUp
    tearDown_local = local.WorkspaceHTTPTest.tearDown
    setUp = remote.WorkspaceRemoteHTTPTest.setUp
    tearDown = remote.WorkspaceRemoteHTTPTest.tearDown
    request = local.WorkspaceHTTPTest.request
    login = local.WorkspaceHTTPTest.login
    allocated = remote.WorkspaceRemoteHTTPTest.allocated

    def api(self, prefix, action, body=None):
        status, _, response = self.request(prefix + action, body)
        self.assertEqual(status, 200, response)
        return response

    def wait(self, prefix, predicate):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            record = self.api(prefix, "")["session"]
            if predicate(record):
                return record
            time.sleep(.01)
        self.fail(str(record))

    def test_remote_commands_queue_dialog_stop_and_auth_use_worker_native_runtime(self):
        script = self.remote_root / "pi-peer"
        script.write_text("#!" + sys.executable + "\n" + FAKE_PI)
        script.chmod(0o700)
        with patch.dict(os.environ, {"EASYREAD_WORKSPACE_PI_COMMAND": str(script),
                                     "PI_CODING_AGENT_DIR": str(self.remote_root / "native-agent")}):
            pid, project = self.allocated()
            record = self.api(project, "/sessions/create", {"engine": "pi"})["session"]
            prefix = project + "/sessions/" + record["id"]
            catalog = self.api(prefix, "/commands", {})
            self.assertEqual(catalog["adapter"], "pi_rpc")
            self.assertTrue(any(c["name"] == "skill:review" for c in catalog["commands"]))
            self.assertFalse(self.app.workspaces.sessions.native.loaded())
            self.assertTrue(self.worker.app.workspaces.sessions.native.loaded(pid, record["id"]))
            for route in ("/follow-up", "/queue/clear", "/commands", "/command", "/respond"):
                self.assertEqual(self.request(prefix + route, {}, auth=False)[0], 401)
            token, self.token = self.token, ""
            self.assertEqual(self.request(prefix + "/commands", {})[0], 403)
            self.token = token
            sent = self.api(prefix, "/send", {"message": "hold"})
            self.assertEqual(sent["submission"]["status"], "submitting")
            self.wait(prefix, lambda r: r.get("active_turn_id") and r["pending_inputs"][-1]["status"] == "delivered")
            queued = self.api(prefix, "/follow-up", {"message": "$review next", "images": [picture()]})
            self.assertEqual(queued["submission"]["status"], "submitting")
            self.wait(prefix, lambda r: r["native_queue"]["follow_up"] == 1)
            self.api(prefix, "/queue/clear", {})
            self.wait(prefix, lambda r: r["pending_inputs"][-1]["status"] == "cleared")
            self.api(prefix, "/cancel", {})
            self.api(prefix, "/send", {"message": "/dialog confirm"})
            record = self.wait(prefix, lambda r: bool(r.get("pending_requests")))
            request = record["pending_requests"][0]
            self.api(prefix, "/respond", {"request_id": request["id"], "generation": request["generation"], "confirmed": False})
            self.wait(prefix, lambda r: r["state"] == "idle")
            self.api(prefix, "/command", {"command": "compact"})
            self.wait(prefix, lambda r: r["pending_inputs"][-1]["status"] == "handled" and r["state"] == "idle")
            self.api(prefix, "/cancel", {})

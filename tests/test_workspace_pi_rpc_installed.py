"""Opt-in installed Pi RPC contract, temp HOME/resources and non-model commands only."""
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from easyread.workspaces.sessions import SessionError
from tests import test_workspace_sessions as fixtures


@unittest.skipUnless(os.environ.get("EASYREAD_PI_SMOKE_COMMAND"), "Set EASYREAD_PI_SMOKE_COMMAND for isolated native RPC validation")
class InstalledPiRPCTest(unittest.TestCase):
    setUp = fixtures.WorkspaceSessionsTest.setUp

    def test_native_inventory_dialog_before_ack_handled_and_compact_rejection(self):
        exe = os.environ["EASYREAD_PI_SMOKE_COMMAND"]
        agent = self.base / "empty-native-agent"
        agent.mkdir()
        extension = agent / "extensions" / "fixture.ts"
        extension.parent.mkdir(parents=True)
        extension.write_text('''export default function(pi) {
          pi.registerCommand("fixture-dialog", {
            description: "Disposable no-model RPC dialog",
            handler: async (_args, ctx) => {
              const value = await ctx.ui.confirm("Native fixture", "No model call");
              ctx.ui.notify(value ? "confirmed" : "declined", "info");
            }
          });
          pi.registerCommand("goal", { description: "Fixture native goal", handler: async (_args, ctx) => ctx.ui.notify("native goal", "info") });
        }''')
        skill = agent / "skills" / "fixture" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: fixture\ndescription: No-model discovery only\n---\nNever execute in this test.\n")
        environment = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.base), "TERM": "dumb",
                       "PI_CODING_AGENT_DIR": str(agent), "PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1",
                       "EASYREAD_WORKSPACE_PI_COMMAND": exe}
        with patch.dict(os.environ, environment, clear=True):
            record = self.sessions.create(self.pid, {"engine": "pi"})["session"]
            sid = record["id"]
            inventory = self.sessions.commands(self.pid, sid)
            self.assertIn({"name": "fixture-dialog", "description": "Disposable no-model RPC dialog", "source": "extension"}, inventory["commands"])
            self.assertTrue(any(c["name"] == "skill:fixture" and c["source"] == "skill" for c in inventory["commands"]))
            response = self.sessions.send(self.pid, sid, {"message": "/fixture-dialog"})
            self.assertEqual(response["submission"]["status"], "submitting")
            end = time.monotonic() + 10
            while time.monotonic() < end:
                current = self.sessions.get(self.pid, sid)["session"]
                if current["pending_requests"]:
                    break
                time.sleep(.01)
            self.assertTrue(current["pending_requests"], current)
            self.assertEqual(current["pending_inputs"][-1]["status"], "submitting")
            request = current["pending_requests"][0]
            self.sessions.respond(self.pid, sid, {"request_id": request["id"], "generation": request["generation"], "confirmed": False})
            while self.sessions.busy(self.pid) and time.monotonic() < end:
                time.sleep(.01)
            current = self.sessions.get(self.pid, sid)["session"]
            self.assertEqual(current["state"], "idle", current)
            self.assertEqual(current["pending_inputs"][-1]["status"], "handled")
            self.assertTrue(any(e.get("text") == "declined" for e in current["events"]))
            runtime = self.sessions.native.runtimes[(self.pid, sid)]
            self.assertIsNone(runtime.rpc.proc.poll())
            # Empty context compaction is rejected without invoking a provider.
            self.sessions.command(self.pid, sid, {"command": "compact"})
            while self.sessions.busy(self.pid) and time.monotonic() < end:
                time.sleep(.01)
            current = self.sessions.get(self.pid, sid)["session"]
            self.assertEqual(current["pending_inputs"][-1]["status"], "failed")
            self.sessions.prepare_export(self.pid)
            self.assertIsNotNone(runtime.rpc.proc.poll())
            # Pi 1.6 awaits session_start before attaching stdin. A startup UI
            # request is real, but claiming its GUI response succeeded is wrong.
            extension.write_text('''export default function(pi) {
              pi.on("session_start", async (_event, ctx) => {
                await ctx.ui.confirm("Startup fixture", "No model call");
              });
            }''')
            startup = self.sessions.create(self.pid, {"engine": "pi"})["session"]
            with self.assertRaises(SessionError) as error:
                self.sessions.commands(self.pid, startup["id"])
            self.assertEqual(error.exception.code, "startup_ui_unsupported")
            self.assertIn("session_start", str(error.exception))
            current = self.sessions.get(self.pid, startup["id"])["session"]
            request = current["pending_requests"][0]
            self.assertTrue(request["unsupported"])
            self.assertTrue(request["expired"])
            blocked = self.sessions.native.runtimes[(self.pid, startup["id"])]
            with patch.object(blocked.rpc, "ui_reply", side_effect=AssertionError("Startup stdin cannot consume an answer")):
                with self.assertRaises(SessionError) as error:
                    self.sessions.respond(self.pid, startup["id"], {"request_id": request["id"],
                                          "generation": request["generation"], "confirmed": False})
            self.assertEqual(error.exception.code, "startup_ui_unsupported")
            self.sessions.cancel(self.pid, startup["id"])
            self.assertIsNotNone(blocked.rpc.proc.poll())

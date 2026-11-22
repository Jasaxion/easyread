"""Memory-only notice scope, acknowledgement and authoritative session projections."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from easyread.research import api
from easyread.server.task_notices import Notices, RequestNotice, notify
from easyread.workspaces.session_store import Records
from easyread.workspaces.sessions import Sessions


class TaskNoticesTest(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.notices = Notices(self.root, history=3)

    def test_scope_history_active_and_snapshot_copy(self):
        for n in range(12):
            self.notices.publish(str(n), "answer", "running", paper_id="paper")
        for n in range(10):
            self.notices.publish(str(n), "answer", "completed", paper_id="paper")
        snapshot = self.notices.snapshot()
        self.assertEqual(len(snapshot), 5)
        self.assertEqual(sum(x["state"] == "running" for x in snapshot), 2)
        self.assertEqual(len(self.notices._keys), 5)
        snapshot[0]["state"] = "changed"
        self.assertNotEqual(self.notices.snapshot()[0]["state"], "changed")
        other = Notices(self.root / "other")
        self.assertEqual(self.notices.snapshot(scope=other.scope), [])
        self.assertIsNone(other.acknowledge(snapshot[0]["id"]))
        self.assertEqual(Notices(self.root).snapshot(), [])  # No durable history.

    def test_ack_is_terminal_only_and_new_attempt_cannot_be_acked_by_stale_id(self):
        first = self.notices.publish("paper:p", "prepare", "queued", paper_id="p")
        self.assertIsNone(self.notices.acknowledge(first["id"]))
        self.notices.publish("paper:p", "prepare", "completed", paper_id="p")
        self.assertIsNone(self.notices.acknowledge(first["id"], scope="foreign"))
        self.assertTrue(self.notices.acknowledge(first["id"])["acknowledged"])
        repeat = self.notices.publish("paper:p", "prepare", "completed", paper_id="p")
        self.assertTrue(repeat["acknowledged"])
        second = self.notices.publish("paper:p", "translate", "queued", paper_id="p")
        self.assertFalse(second["acknowledged"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertIsNone(self.notices.acknowledge(first["id"]))
        self.assertEqual(len(self.notices.snapshot()), 1)

    def test_delayed_paper_write_cannot_overwrite_a_later_write_or_new_attempt(self):
        emit = lambda state, order, **kw: self.notices.observe("paper", "paper", {
            "type": "translate", "state": state, "_notice_order": order}, **kw)
        emit("running", 10)
        emit("done", 12)
        terminal = self.notices.snapshot()[0]
        emit("running", 11)
        self.assertEqual(self.notices.snapshot()[0], terminal)
        emit("queued", 14, restart=True)
        new = self.notices.snapshot()[0]
        emit("done", 13)
        self.assertEqual(self.notices.snapshot()[0], new)
        self.assertNotEqual(new["id"], terminal["id"])

    def test_bounded_scalars_safe_destination_and_no_prompt_fields(self):
        task = self.notices.publish("x", "reader-chat", "running", title="title " * 1000,
                                    message="message " * 1000, url="//evil.example", done=True, total=-1)
        self.assertEqual(task["url"], "/research")
        self.assertLessEqual(len(task["title"]), 160)
        self.assertLessEqual(len(task["message"]), 240)
        self.assertNotIn("done", task)
        self.assertNotIn("total", task)
        for key in ("created", "updated"):
            self.assertIsInstance(task[key], float)
        self.assertTrue(task["id"].startswith("activity-"))
        notify(self.notices.observe, "paper", "paper", {"state": "running", "type": "translate",
               "phase": "model", "translation_extra": "private prompt", "error": "secret key",
               "usage": {"huge": "payload"}, "done": 1, "total": 4})
        task = self.notices.snapshot()[0]
        self.assertEqual((task["done"], task["total"]), (1, 4))
        self.assertNotIn("private prompt", str(task))
        self.assertNotIn("secret key", str(task))
        self.assertNotIn("payload", str(task))

    def test_monitor_never_initializes_or_reads_producers_or_files(self):
        app = SimpleNamespace(lib=SimpleNamespace(root=self.root), notices=self.notices,
                              token="token", workspaces=None)
        with patch("pathlib.Path.resolve", side_effect=AssertionError("resolve on poll")), \
             patch("pathlib.Path.open", side_effect=AssertionError("file on poll")), \
             patch("easyread.server.workspaces_api.manager", side_effect=AssertionError("manager on poll")), \
             patch.object(Sessions, "_load", side_effect=AssertionError("history on poll")), \
             patch("easyread.library.library.Library.all", side_effect=AssertionError("library on poll")), \
             patch("easyread.engines.engines.run", side_effect=AssertionError("AI on poll")):
            result = api.get(app, "/api/research/v1/monitor")
        self.assertEqual(result, {"scope_id": self.notices.scope, "tasks": [], "activities": [], "token": "token"})
        self.assertIsNone(app.workspaces)

    def test_optional_observer_exception_does_not_change_request_result(self):
        app = SimpleNamespace(notices=SimpleNamespace(publish=Mock(side_effect=RuntimeError("observer"))))
        with RequestNotice(app, "ai-query"):
            pass
        app.notices.publish.assert_called()
        with self.assertRaisesRegex(ValueError, "real error"):
            with RequestNotice(app, "ai-query"):
                raise ValueError("real error")

    def test_session_write_uses_summaries_only_after_successful_save(self):
        records = Records(Mock(history_record=Mock(side_effect=AssertionError("history loaded"))),
                          {"s1": {"id": "s1", "state": "running", "engine": "pi"},
                           "s2": {"id": "s2", "state": "idle", "engine": "claude"}})
        sessions = Sessions(Mock(), observer=self.notices.observe)
        with patch.object(sessions, "_journal") as journal:
            sessions._write("project", {"sessions": records})
            self.assertEqual(len(self.notices.snapshot()), 1)
            journal.return_value.save.side_effect = OSError("disk full")
            dict.__getitem__(records, "s1")["state"] = "error"
            with self.assertRaises(OSError):
                sessions._write("project", {"sessions": records})
            self.assertEqual(self.notices.snapshot()[0]["state"], "running")
            journal.return_value.save.side_effect = None
            sessions.observer = Mock(side_effect=RuntimeError("observer failed"))
            sessions._write("project", {"sessions": records})
        self.assertEqual(records.loaded, set())

    def test_session_lifecycle_idle_settings_settling_waiting_and_restart(self):
        record = {"id": "session", "engine": "pi", "state": "idle", "runtime_state": "awake"}
        data = {"sessions": {"session": record}}
        emit = lambda: self.notices.observe("sessions", "project", data)
        emit()
        self.assertEqual(self.notices.snapshot(), [])
        record.update(state="queued", runtime_phase="submitting")
        emit()
        first = self.notices.snapshot()[0]
        for state, phase, expected in (("running", "running", "running"), ("running", "retrying", "running"),
                                       ("running", "settling", "running"), ("waiting_user", "waiting_user", "waiting"),
                                       ("waiting_external", "", "waiting"), ("paused", "", "waiting")):
            record.update(state=state, runtime_phase=phase)
            emit()
            current = self.notices.snapshot()[0]
            self.assertEqual(current["state"], expected)
            self.assertEqual(current["id"], first["id"])
        record.update(state="idle", runtime_phase="idle")
        emit()
        terminal = self.notices.acknowledge(first["id"])
        record.update(title="changed settings", updated="later", error="old error", state="error")
        emit()
        self.assertEqual(self.notices.snapshot()[0], terminal)
        record.update(state="running", error="", goal={"status": "active"})
        emit()
        self.assertNotEqual(self.notices.snapshot()[0]["id"], first["id"])
        self.assertFalse(self.notices.snapshot()[0]["acknowledged"])
        record.update(state="interrupted")
        emit()
        self.assertEqual(self.notices.snapshot()[0]["state"], "interrupted")

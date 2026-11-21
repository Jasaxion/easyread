"""Incremental session storage, bounded queries and non-destructive legacy reads."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from easyread.workspaces import session_store
from easyread.workspaces.sessions import Sessions, SessionError
from easyread.workspaces.store import Store


class WorkspaceSessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="easyread-session-journal-")
        self.store = Store(Path(self.tmp.name) / "library")
        self.pid = self.store.allocate({"name": "Journal fixture"})["id"]
        self.sessions = Sessions(self.store)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.sessions.close)

    def test_legacy_full_history_preserved_bounded_paging_and_incremental_queries(self):
        sid = "abcdef123456"
        raw = json.dumps({"version": 1, "seq": 5, "order": [sid], "sessions": {sid: {
            "id": sid, "engine": "codex", "state": "idle", "title": "Legacy", "native_id": "",
            "messages": [{"role": "user", "content": str(i)} for i in range(85)],
            "events": [{"kind": "legacy", "text": str(i)} for i in range(211)]}}})
        path = self.sessions._file(self.pid)
        path.write_text(raw)
        snapshot = self.sessions.get(self.pid, sid)
        self.assertEqual(path.read_text(), raw)
        self.assertEqual(len(snapshot["session"]["messages"]), 40)
        self.assertEqual(len(snapshot["session"]["events"]), 200)
        messages = {m["id"]: m for m in snapshot["session"]["messages"]}
        events = {e["seq"]: e for e in snapshot["session"]["events"]}
        before = snapshot["history_before"]
        pages = 0
        while before is not None:
            page = self.sessions.history(self.pid, sid, before, 40)
            self.assertLessEqual(len(page["messages"]), 40)
            self.assertLessEqual(len(page["events"]), 200)
            messages.update({m["id"]: m for m in page["messages"]})
            events.update({e["seq"]: e for e in page["events"]})
            before = page["history_before"]
            pages += 1
            self.assertLess(pages, 8)
        self.assertEqual((len(messages), len(events)), (85, 211))
        with patch.object(session_store.Journal, "history_record", side_effect=AssertionError("No hydration on poll/list")), \
                patch.object(self.sessions, "capabilities", return_value={"engines": {e: {} for e in session_store.cli.ENGINES}}), \
                patch("easyread.workspaces.session_cli.engine_status", side_effect=lambda e, t: {"id": e, "capabilities": {}}):
            listing = self.sessions.listing(self.pid)
            self.assertNotIn("messages", listing["sessions"][0])
            self.assertNotIn("events", listing["sessions"][0])
            self.assertEqual(self.sessions.updates(self.pid, sid, snapshot["cursor"])["messages"], [])
        self.sessions.rename(self.pid, sid, {"title": "Renamed"})
        delta = self.sessions.updates(self.pid, sid, snapshot["cursor"], 1)
        self.assertGreater(delta["next_cursor"], snapshot["cursor"])
        self.assertEqual(delta["messages"], [])
        self.assertEqual(delta["session"]["title"], "Renamed")
        self.assertEqual(path.read_text(), raw)
        full = self.sessions._load(self.pid, full=True)["sessions"][sid]
        self.assertEqual((len(full["messages"]), len(full["events"])), (85, 211))

    def test_restart_expires_questions_and_never_replays_goal(self):
        sid = self.sessions.create(self.pid, {"engine": "codex"})["session"]["id"]
        self.sessions._commit(self.pid, lambda d: d["sessions"][sid].update(
            state="waiting_user", runtime_state="awake", active_turn_id="turn-1", goal={"objective": "research", "status": "active"},
            pending_requests=[{"id": 1, "generation": "old", "kind": "question", "thread_id": "native", "is_blocking": True}]))
        restarted = Sessions(self.store)
        try:
            with patch("easyread.workspaces.codex_protocol.AppServer", side_effect=AssertionError("No OS resurrection")):
                record = restarted.get(self.pid, sid)["session"]
                self.assertEqual(record["state"], "interrupted")
                self.assertTrue(record["reconciliation_required"])
                self.assertTrue(record["pending_requests"][0]["expired"])
                self.assertEqual(record["pending_count"], 0)
                self.assertEqual(record["runtime_state"], "sleeping")
                with self.assertRaises(SessionError):
                    restarted.send(self.pid, sid, {"message": "do not duplicate experiments"})
        finally:
            restarted.close()

    def test_bad_legacy_file_is_never_replaced_and_journal_link_is_rejected(self):
        path = self.sessions._file(self.pid)
        path.write_text("{broken")
        with self.assertRaises(SessionError):
            self.sessions.listing(self.pid)
        self.assertEqual(path.read_text(), "{broken")
        journal = self.sessions._journal(self.pid).path
        journal.unlink()
        outside = Path(self.tmp.name) / "outside.sqlite"
        outside.write_bytes(b"do not open")
        os.link(outside, journal)
        with self.assertRaises(SessionError):
            self.sessions.get(self.pid, "abcdef123456")
        self.assertEqual(outside.read_bytes(), b"do not open")

    def test_stream_journal_stores_message_references_not_repeated_full_answers(self):
        sid = self.sessions.create(self.pid, {"engine": "claude"})["session"]["id"]
        self.sessions._commit(self.pid, lambda d: d["sessions"][sid]["messages"].append({"id": "answer", "role": "assistant", "content": "a"}))
        initial = self.sessions.get(self.pid, sid)["cursor"]
        for i in range(3):
            self.sessions._commit(self.pid, lambda d, i=i: d["sessions"][sid]["messages"][-1].update(content="x" * (1000 * (i + 1))))
        delta = self.sessions.updates(self.pid, sid, initial, 2)
        self.assertTrue(delta["has_more"])
        self.assertEqual(len(delta["messages"]), 1)
        self.assertEqual(len(delta["messages"][0]["content"]), 3000)
        with self.sessions._journal(self.pid).connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM journal WHERE kind='message' AND payload IS NOT NULL").fetchone()[0], 0)
        self.assertTrue(self.sessions.updates(self.pid, sid, 999999)["reset"])


if __name__ == "__main__":
    unittest.main()

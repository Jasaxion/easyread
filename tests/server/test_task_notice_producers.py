"""Lifecycle observers piggyback existing writes, never control execution."""
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from easyread.library.library import Library
from easyread.library.store import Workspace, write_json_atomic
from easyread.server import babeldoc_api, server, small_jobs
from easyread.server.jobs import Jobs
from easyread.server.task_notices import Notices
from easyread.server.task_notice_sources import candidate_notice


class TaskNoticeProducersTest(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lib = Library(Path(tmp.name))
        self.notices = Notices(self.lib.root)
        with patch("easyread.server.jobs.threading.Thread.start"), \
             patch("easyread.server.jobs.config.load", return_value={}):
            self.jobs = Jobs(self.lib, observer=self.notices.observe)
        cfg = patch("easyread.server.jobs.config.load", return_value={"engine": "codex", "page_cap": 60})
        cfg.start()
        self.addCleanup(cfg.stop)
        root = self.lib.root / "paper"
        root.mkdir()
        self.ws = Workspace(root)
        write_json_atomic(self.ws.paper_path, {"meta": {}, "translation": {}, "blocks": []})

    def task(self):
        self.assertEqual(len(self.notices.snapshot()), 1)
        return self.notices.snapshot()[0]

    def test_paper_prepare_reservation_handoff_confirm_complete_and_new_attempt(self):
        ws = self.ws
        self.jobs.enqueue(ws, translate_after=False)
        first = self.task()
        self.jobs.enqueue(ws)  # Reservation upgrades the same logical attempt.
        self.assertEqual(self.task()["id"], first["id"])
        def prepare(ws):
            ws.update("paper", lambda p: p["meta"].update(pages=[{"n": 1}, {"n": 2}]))
        with patch("easyread.server.jobs.translate.prepare", side_effect=prepare):
            self.jobs._process_bulk(ws.id, "prepare")
        self.assertEqual((self.task()["id"], self.task()["state"]), (first["id"], "queued"))
        with patch("easyread.server.jobs.config.load", return_value={"engine": "codex", "page_cap": 1}):
            self.jobs._process_bulk(ws.id)
        self.assertEqual(self.task()["state"], "waiting")
        self.assertIsNone(self.notices.acknowledge(first["id"]))
        self.jobs.enqueue(ws, confirmed=True)
        self.assertEqual(self.task()["id"], first["id"])
        with patch("easyread.server.jobs.translate.translate_pages", return_value={}):
            self.jobs._process_bulk(ws.id)
        self.assertEqual(self.task()["state"], "completed")
        self.notices.acknowledge(first["id"])
        self.jobs.enqueue(ws)
        self.assertNotEqual(self.task()["id"], first["id"])
        self.assertFalse(self.task()["acknowledged"])

    def test_enqueue_and_write_publish_only_after_success_and_observer_is_isolated(self):
        with patch.object(self.ws, "update", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.jobs.enqueue(self.ws)
            with self.assertRaises(OSError):
                self.jobs._write(self.ws, state="running")
        self.assertEqual(self.notices.snapshot(), [])
        self.jobs.observer = Mock(side_effect=RuntimeError("observer failed"))
        self.jobs.enqueue(self.ws, translate_after=False)
        self.jobs._write(self.ws, state="done")
        self.assertEqual(self.ws.load("job")["state"], "done")
        self.jobs.observer.assert_called()

    def test_resume_seeds_waiting_without_an_additional_library_scan(self):
        self.ws.update("job", lambda j: j.update(type="translate", state="confirm", total=80))
        with patch.object(self.lib, "all", wraps=self.lib.all) as listing, \
             patch("easyread.server.jobs.threading.Thread.start"):
            Jobs(self.lib, observer=self.notices.observe)
        listing.assert_called_once()
        self.assertEqual(self.task()["state"], "waiting")

    def test_figure_acceptance_and_partial_terminal(self):
        with patch("easyread.server.jobs.config.load", return_value={"figure_images": {"enabled": True}}), \
             patch("easyread.server.figure_jobs.select", return_value=["fig1", "fig2"]):
            self.jobs.enqueue_figures(self.ws, {})
        self.assertEqual(self.task()["activity_kind"], "figure-images")
        self.jobs._write(self.ws, state="partial", done=1, total=2, error="private model diagnostics")
        self.assertEqual(self.task()["state"], "failed")
        self.assertNotIn("private model diagnostics", str(self.task()))

    def test_small_answer_and_retranslate_exclude_smart_annotation_duplicate(self):
        for kind in ("answer", "retranslate"):
            job = self.jobs.submit_small(kind, self.ws.id, note="private", key="p1", hint="private hint")
            with patch.object(self.jobs.small, "get", side_effect=[job, StopIteration]), \
                 patch("easyread.server.small_jobs.translate.answer"), \
                 patch("easyread.server.small_jobs.translate.retranslate"):
                with self.assertRaises(StopIteration):
                    small_jobs.run(self.jobs)
        self.assertEqual(len(self.notices.snapshot()), 2)
        self.assertEqual({x["state"] for x in self.notices.snapshot()}, {"completed"})
        with patch("easyread.research.tasks.register_external"):
            self.jobs.submit_small("smart-annotate", self.ws.id)
        self.assertEqual(len(self.notices.snapshot()), 2)
        self.assertNotIn("private", str(self.notices.snapshot()))

    def test_small_error_and_cancel_are_not_completed(self):
        for cancelled in (False, True):
            job = self.jobs.submit_small("answer", self.ws.id, note="private")
            if cancelled:
                self.jobs.small_cancels[job["id"]].set()
            with patch.object(self.jobs.small, "get", side_effect=[job, StopIteration]), \
                 patch("easyread.server.small_jobs.translate.answer", side_effect=RuntimeError("secret")):
                with self.assertRaises(StopIteration):
                    small_jobs.run(self.jobs)
            self.assertEqual(self.notices.snapshot()[0]["state"], "cancelled" if cancelled else "failed")
        self.assertNotIn("secret", str(self.notices.snapshot()))

    def test_babeldoc_start_success_failure_cached_reuse_and_observer_failure(self):
        source = self.ws.root / "source.pdf"
        source.write_bytes(b"%PDF-fake")
        ws = SimpleNamespace(id=self.ws.id, root=self.ws.root, source_pdf=source,
                             load=lambda _: {"meta": {"source": "paper.pdf"}})
        jobs = babeldoc_api.BabelDocJobs(observer=self.notices.observe)
        cfg = {"engine": "codex", "codex": {}}
        with patch("easyread.server.jobs.engine_for", return_value=cfg), \
             patch.object(babeldoc_api.engines, "cli_path", return_value="fake-codex"), \
             patch.object(babeldoc_api.babeldoc_runtime, "python_path", return_value="fake-python"), \
             patch.object(babeldoc_api.threading, "Thread") as thread:
            jobs.start(ws)
        self.assertEqual(self.task()["state"], "running")
        args = thread.call_args.kwargs["args"]
        def generate(command, **kwargs):
            output = Path(command[command.index("--output") + 1])
            (output / "out.mono.pdf").write_bytes(b"%PDF-fake")
            return subprocess.CompletedProcess(command, 0, "", "")
        with patch.object(babeldoc_api.subprocess, "run", side_effect=generate):
            jobs._run(*args)
        self.assertEqual(self.task()["state"], "completed")
        before = self.notices.snapshot()
        with patch.object(babeldoc_api.threading, "Thread") as thread:
            jobs.start(ws)
        thread.assert_not_called()
        self.assertEqual(self.notices.snapshot(), before)
        with patch.object(babeldoc_api.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "secret")):
            jobs._run(ws, "fake", cfg, "fake", "second", "paper")
        self.assertEqual(self.notices.snapshot()[0]["state"], "failed")
        self.assertNotIn("secret", str(self.notices.snapshot()))
        jobs.observer = Mock(side_effect=RuntimeError("observer"))
        with patch.object(babeldoc_api.subprocess, "run", side_effect=generate):
            jobs._run(ws, "fake", cfg, "fake", "third", "paper")
        self.assertEqual(jobs.status(ws)["state"], "done")

    def test_candidate_claim_status_is_summary_only(self):
        app = SimpleNamespace(notices=self.notices)
        item = {"paper_key": "openalex:W1", "paper": {"title": "Paper", "abstract": "private"}}
        candidate_notice(app, item, "running")
        self.assertEqual(self.task()["title"], "Paper")
        candidate_notice(app, item, "completed", accepted=False)
        self.assertEqual(self.task()["state"], "interrupted")
        self.assertNotIn("private", str(self.task()))
        candidate_notice(app, None, "running")  # Malformed observer data cannot escape.

    def test_app_initializes_registry_before_job_workers_and_workspaces_stays_lazy(self):
        with patch.object(server, "Access"), patch.object(server, "Terminals"), \
             patch.object(server, "Library", return_value=self.lib), \
             patch.object(server, "Jobs") as jobs, patch.object(server, "BabelDocJobs"), \
             patch.object(server.library_api, "LibraryLocation"), patch.object(server, "PublicAPI"), \
             patch.object(server.research_api, "recover"):
            def constructor(lib, observer):
                observer("paper", "recovered", {"type": "translate", "state": "confirm"})
            jobs.side_effect = constructor
            app = server.App({"library_dir": str(self.lib.root)})
        self.assertEqual(app.notices.snapshot()[0]["state"], "waiting")
        self.assertIsNone(app.workspaces)

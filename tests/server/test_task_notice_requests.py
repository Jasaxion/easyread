"""Authenticated monitor/ack and unchanged request/NDJSON results with fake AI."""
from contextlib import ExitStack, nullcontext
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from tests.http_support import anonymous_access
from easyread.chat import notehelp
from easyread.engines.engines import Cancelled, EngineError
from easyread.library.store import Workspace
from easyread.server import chat_handler, research_api
from easyread.server.server import Handler
from easyread.server.task_notices import Notices


class TaskNoticeRequestsTest(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ws = Workspace(self.root)
        self.notices = Notices(self.root)
        self.app = SimpleNamespace(access=anonymous_access(self.root), token="test-token", notices=self.notices,
                                   lib=SimpleNamespace(root=self.root, ws=lambda pid: self.ws), workspaces=None,
                                   location=SimpleNamespace(request=lambda *args: nullcontext(), status="idle"))
        cls = type("NoticeHandler", (Handler,), {"app": self.app, "log_message": lambda *args: None})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), cls)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, token="test-token"):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request(method, path, json.dumps(body or {}) if method == "POST" else None,
                         {"X-Token": token, "Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_get_token_without_existing_token_and_post_ack_scope_guard(self):
        task = self.notices.publish("a", "answer", "completed", paper_id="paper")
        code, raw = self.request("GET", "/api/research/v1/monitor", token="")
        result = json.loads(raw)
        self.assertEqual(code, 200)
        self.assertEqual(result["token"], "test-token")
        self.assertEqual(result["activities"], [task])
        self.assertIn("tasks", result)
        path = "/api/research/v1/activities/" + task["id"] + "/ack"
        self.assertEqual(self.request("POST", path, token="wrong")[0], 403)
        self.assertEqual(self.request("POST", path, {"scope_id": "foreign"})[0], 400)
        code, raw = self.request("POST", path, {"scope_id": result["scope_id"]})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(raw)["task"]["acknowledged"])
        self.assertIsNone(self.app.workspaces)
        self.app.access.update({"enabled": True, "code": "unit-test-code"})
        self.assertEqual(self.request("GET", "/api/research/v1/monitor", token="")[0], 401)

    def fake_ai(self, stream):
        stack = ExitStack()
        cfg, model = {"engine": "codex", "codex": {}}, {"id": "test", "engine": "codex", "name": "Fake"}
        for target, result in (("easyread.app.config.load", cfg),
                               ("easyread.chat.chat_models.engine_cfg", (cfg, model)),
                               ("easyread.chat.chat_models.require_qa_model", None),
                               ("easyread.chat.chat.prompt", "private prompt"),
                               ("easyread.chat.chat.receive_images", []),
                               ("easyread.chat.chat.image_paths", []),
                               ("easyread.chat.chat_store.get", None),
                               ("easyread.chat.chat_store.new_id", "thread"),
                               ("easyread.chat.chat_store.append", {"id": "message"}),
                               ("easyread.chat.notehelp.prompt", "private note")):
            stack.enter_context(patch(target, return_value=result))
        stack.enter_context(patch.object(self.ws, "load", return_value={"meta": {}}))
        stack.enter_context(patch("easyread.chat.chat.stream", side_effect=stream))
        return stack

    def test_post_chat_and_notehelp_stream_success_error_and_engine_cancel(self):
        for endpoint, body in (("chat", {"text": "private question"}), ("notehelp", {"mode": "draft"})):
            for state, error in (("completed", None), ("failed", EngineError("controlled failure")),
                                 ("cancelled", Cancelled())):
                with self.subTest(endpoint=endpoint, state=state):
                    def stream(*args, **kwargs):
                        yield "chunk"
                        if error is not None:
                            raise error
                    with self.fake_ai(stream):
                        code, raw = self.request("POST", "/api/p/paper/" + endpoint, body)
                    self.assertEqual(code, 200)
                    lines = [json.loads(line) for line in raw.splitlines()]
                    self.assertIn({"t": "chunk"}, lines)
                    self.assertEqual(any(x.get("done") is True for x in lines), state == "completed")
                    self.assertEqual(any("error" in x for x in lines), state == "failed")
                    task = self.notices.snapshot()[0]
                    self.assertEqual(task["state"], state)
                    self.assertNotIn("private", str(task))
                    self.assertNotIn("controlled failure", str(task))

    def test_disconnected_stream_explicitly_marks_cancelled_not_completed(self):
        for handle, body in ((chat_handler.handle, {"text": "question"}), (notehelp.handle, {"mode": "draft"})):
            handler = SimpleNamespace(app=self.app, wfile=Mock(write=Mock(side_effect=BrokenPipeError)),
                                      send_response=Mock(), send_header=Mock(), end_headers=Mock())
            with self.fake_ai(lambda *args, **kwargs: iter(["chunk"])):
                handle(handler, self.ws, body)
            self.assertEqual(self.notices.snapshot()[0]["state"], "cancelled")

    def test_request_validation_failure_and_selection_model_failure(self):
        for failure in (ValueError("invalid selection"), EngineError("controlled"), Cancelled()):
            with patch("easyread.translate.selection.translate", side_effect=failure):
                self.request("POST", "/api/p/paper/translate-selection", {"text": "word"})
            expected = "cancelled" if isinstance(failure, Cancelled) else "failed"
            self.assertEqual(self.notices.snapshot()[0]["state"], expected)
        with patch("easyread.translate.selection.translate", return_value={"translation": "word"}):
            code, raw = self.request("POST", "/api/p/paper/translate-selection", {"text": "word"})
        self.assertEqual((code, json.loads(raw)), (200, {"translation": "word"}))
        self.assertEqual(self.notices.snapshot()[0]["state"], "completed")

    def test_library_chat_keeps_results_and_records_returned_model_errors(self):
        self.app.lib.passage_index = object()
        for result, state in (({"answer": "result", "sources": []}, "completed"),
                              ({"answer": "", "sources": [], "error": "private diagnostic"}, "failed")):
            with patch("easyread.chat.library_search.answer", return_value=result):
                code, raw = self.request("POST", "/api/library/ask", {"query": "private question"})
            self.assertEqual((code, json.loads(raw)), (200, result))
            task = self.notices.snapshot()[0]
            self.assertEqual((task["activity_kind"], task["state"], task["url"]), ("library-chat", state, "/"))
            self.assertNotIn("private", str(task))

    def test_synchronous_research_helpers_and_async_nonduplication(self):
        for endpoint in ("/ai-summary", "/ai-rewrite", "/ai-query", "/ai-category-explain"):
            with patch.object(research_api.workspace, "post", return_value={"text": "unchanged"}):
                code, raw = self.request("POST", "/api/research/v2" + endpoint, {"papers": [{"title": "Paper"}]})
            self.assertEqual((code, json.loads(raw)), (200, {"text": "unchanged"}))
            self.assertEqual(self.notices.snapshot()[0]["activity_kind"], endpoint[1:])
            self.assertEqual(self.notices.snapshot()[0]["state"], "completed")
        with patch.object(research_api.workspace, "post", side_effect=ValueError("failure")):
            self.assertEqual(self.request("POST", "/api/research/v2/ai-query", {})[0], 400)
        self.assertEqual(self.notices.snapshot()[0]["state"], "failed")
        before = self.notices.snapshot()
        with patch.object(research_api.workspace, "post", return_value={"task": {"id": "original"}}):
            self.request("POST", "/api/research/v2/reading-path", {})
        self.assertEqual(self.notices.snapshot(), before)

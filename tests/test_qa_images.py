"""Clipboard transport across HTTP, native adapters and library search; no inference."""
import base64
from contextlib import nullcontext
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from PIL import Image

from easyread.chat import images, library_search
from easyread.engines import engines
from easyread.server import workspaces_api
from easyread.workspaces import session_images, session_cli as cli
from tests.http_support import anonymous_access
from tests import test_workspace_codex as codex_fixture
from tests import test_workspace_sessions as session_fixture


def picture(format="PNG"):
    out = io.BytesIO()
    Image.new("RGB", (12, 10), "green").save(out, format)
    return "data:image/" + {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp"}[format] + ";base64," + base64.b64encode(out.getvalue()).decode()


class WorkspaceImageHTTPTest(unittest.TestCase):
    setUp = codex_fixture.WorkspaceCodexTest.setUp
    until = codex_fixture.WorkspaceCodexTest.until
    snapshot = codex_fixture.WorkspaceCodexTest.snapshot
    runtime = codex_fixture.WorkspaceCodexTest.runtime
    rpc = codex_fixture.WorkspaceCodexTest.rpc

    def server(self):
        from easyread.server.server import Handler
        app = SimpleNamespace(access=anonymous_access(self.base), token="test-token",
            lib=SimpleNamespace(root=self.store.libroot),
            workspaces=SimpleNamespace(store=self.store, sessions=self.sessions),
            location=SimpleNamespace(status="idle", request=lambda *args: nullcontext()))
        handler = type("ImageHandler", (Handler,), {"app": app, "log_message": lambda *args: None})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}/api/workspaces/{self.pid}/sessions/{self.sid}"

    def post(self, operation, body):
        request = urllib.request.Request(self.url + "/" + operation, json.dumps(body).encode(),
                                         {"Content-Type": "application/json", "X-Token": "test-token"})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_http_codex_send_and_steer_forward_local_images_and_keep_history(self):
        self.server()
        self.post("send", {"images": [picture("JPEG")]})
        turn = self.until(lambda: self.snapshot()["session"].get("active_turn_id"))
        message = self.snapshot()["session"]["messages"][0]
        self.assertTrue(message["content"])
        image_path = self.store.root(self.pid) / message["images"][0]
        sent = next(c["params"]["input"] for c in self.rpc("_test/calls")["calls"] if c["method"] == "turn/start")
        self.assertEqual(sent[-1], {"type": "localImage", "path": str(image_path)})
        self.assertNotIn("base64", sent[0]["text"])
        with Image.open(image_path) as actual:
            self.assertEqual(actual.format, "PNG")
        before = set(image_path.parent.iterdir())
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post("send", {"message": "busy", "images": [picture()]})
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(set(image_path.parent.iterdir()), before, "unaccepted files are removed")
        self.post("steer", {"message": "compare", "images": [picture("WEBP")], "expected_turn_id": turn})
        steer = next(c["params"]["input"] for c in self.rpc("_test/calls")["calls"] if c["method"] == "turn/steer")
        self.assertEqual(steer[-1]["type"], "localImage")
        self.assertTrue(Path(steer[-1]["path"]).is_file())
        reopened = self.sessions.get(self.pid, self.sid)["session"]["messages"]
        self.assertEqual(len([m for m in reopened if m.get("images")]), 2)
        self.rpc("_test/complete"); self.until(lambda: not self.runtime().active_turn)
        self.post("send", {"message": "follow up"})
        latest = [c for c in self.rpc("_test/calls")["calls"] if c["method"] == "turn/start"][-1]
        self.assertEqual(latest["params"]["input"], [{"type": "text", "text": "follow up"}])
        self.assertEqual(self.sessions.get(self.pid, self.sid)["session"]["messages"][0]["images"], message["images"])

    def test_invalid_data_paths_goals_and_scoped_body_limit(self):
        self.server()
        for body in ({"images": ["../../secret.png"]}, {"images": [picture()] * 5},
                     {"images": ["data:image/png;base64,AAAAA"]},
                     {"images": [picture()], "mode": "goal"},
                     {"images": [picture()], "message": "/goal pause"}):
            with self.subTest(body=str(body)[:50]), self.assertRaises(urllib.error.HTTPError) as error:
                self.post("send", body)
            self.assertEqual(error.exception.code, 400)
        self.assertFalse((self.store.root(self.pid) / "chat-media").exists())
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.post("goal", {"action": "set", "objective": "study", "images": [picture()]})
        self.assertEqual(error.exception.code, 400)
        self.post("send", {"message": "ordinary", "_image_names": ["../../secret.png"]})
        sent = next(c["params"]["input"] for c in self.rpc("_test/calls")["calls"] if c["method"] == "turn/start")
        self.assertEqual(sent, [{"type": "text", "text": "ordinary"}])
        with self.assertRaises(ValueError):
            session_images.paths(self.store.root(self.pid), ["../secret.png"])
        # The larger allowance is opted into only for send/steer, before body reads.
        handler = SimpleNamespace(headers={"Content-Length": str(10 * 1024 * 1024)}, _body=lambda: b"{}")
        with self.assertRaises(ValueError): workspaces_api._body(handler)
        self.assertEqual(workspaces_api._body(handler, images=True), {})
        handler.headers["Content-Length"] = str(images.MAX_REQUEST + 1)
        with self.assertRaises(ValueError): workspaces_api._body(handler, images=True)

    def test_symlink_attachment_directory_is_rejected_without_touching_target(self):
        outside = self.base / "outside"; outside.mkdir()
        folder = self.store.root(self.pid) / "chat-media"
        try: folder.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError): self.skipTest("symlinks unavailable")
        with self.assertRaises(ValueError):
            self.sessions.send(self.pid, self.sid, {"message": "unsafe", "images": [picture()]})
        self.assertEqual(list(outside.iterdir()), [])
        folder.unlink(); folder.write_text("existing project file")
        with self.assertRaises(cli.SessionError):
            self.sessions.send(self.pid, self.sid, {"images": [picture()]})
        self.assertEqual(folder.read_text(), "existing project file")


class ClaudeImageTest(unittest.TestCase):
    setUp = session_fixture.WorkspaceSessionsTest.setUp

    def test_claude_receives_read_image_paths_on_stdin_not_image_argv(self):
        class Input(io.StringIO):
            def close(self): pass
        record = self.sessions.create(self.pid, {"engine": "claude"})["session"]
        process = session_fixture.ControlledProcess(record["native_id"]); process.stdin = Input()
        with patch.object(cli, "executable", return_value="fake-claude"), \
                patch("easyread.workspaces.sessions.subprocess.Popen", return_value=process) as launch:
            self.sessions.send(self.pid, record["id"], {"images": [picture()]})
            self.sessions._jobs[(self.pid, record["id"])].thread.join(5)
        saved = self.sessions.get(self.pid, record["id"])["session"]["messages"][0]
        self.assertIn(saved["images"][0], process.stdin.getvalue())
        self.assertIn("native read/image tool", process.stdin.getvalue())
        self.assertNotIn("base64", process.stdin.getvalue())
        self.assertFalse(any("base64" in arg or "chat-media" in arg for arg in launch.call_args.args[0]))
        self.assertTrue((self.store.root(self.pid) / saved["images"][0]).is_file())


class LibraryImageTest(unittest.TestCase):
    def test_library_http_forwards_images_to_answer_adapter(self):
        from easyread.server.server import Handler
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = SimpleNamespace(access=anonymous_access(root), token="test-token",
                lib=SimpleNamespace(root=root, passage_index=SimpleNamespace(snapshot=lambda _: ([], 0))),
                location=SimpleNamespace(status="idle", request=lambda *args: nullcontext()))
            handler = type("LibraryImageHandler", (Handler,), {"app": app, "log_message": lambda *args: None})
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            seen = []
            def stream(*args, **kwargs):
                seen.extend(kwargs["images"])
                self.assertTrue(seen[0].read_bytes().startswith(b"\x89PNG"))
                return iter(["local image acknowledged"])
            try:
                with patch.object(library_search.config, "load", return_value={}), \
                        patch.object(library_search.chat_models, "engine_cfg", return_value=({"engine": "codex", "codex": {}}, {"name": "fake"})), \
                        patch.object(library_search.chat, "stream", side_effect=stream):
                    request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/library/ask",
                        json.dumps({"images": [picture()]}).encode(), {"Content-Type": "application/json", "X-Token": "test-token"})
                    with urllib.request.urlopen(request) as response:
                        self.assertEqual(json.load(response)["answer"], "local image acknowledged")
                self.assertTrue(seen)
                self.assertFalse(seen[0].exists())
            finally:
                server.shutdown(); server.server_close()

    def test_search_images_reach_retrieval_and_answer_then_cleanup_on_success_or_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                library = SimpleNamespace(root=root)
                index = SimpleNamespace(snapshot=lambda _: ([{"text": "some paper"}], 0))
                paths = []
                def stream(cfg, prompt, cwd, cancel, **kwargs):
                    paths.extend(kwargs["images"])
                    self.assertTrue(all(path.is_file() for path in kwargs["images"]))
                    self.assertNotIn("base64", prompt)
                    if fail: raise engines.EngineError("controlled failure")
                    return iter(["terms or answer"])
                with patch.object(library_search.config, "load", return_value={}), \
                        patch.object(library_search.chat_models, "engine_cfg", return_value=({"engine": "codex", "codex": {}}, {"name": "fake"})), \
                        patch.object(library_search, "rank", return_value=[{"title": "paper", "text": "excerpt"}]), \
                        patch.object(library_search.chat, "stream", side_effect=stream) as transport:
                    result = library_search.answer(library, index, {"images": [picture()]})
                    self.assertEqual(transport.call_count, 1 if fail else 2)
                    self.assertEqual("error" in result, fail)
                    before = transport.call_count
                    with self.assertRaises(ValueError): library_search.answer(library, index, {"images": ["../escape"]})
                    self.assertEqual(transport.call_count, before)
                self.assertTrue(paths)
                self.assertTrue(all(not path.exists() for path in paths))

    def test_image_only_search_without_index_still_forwards_attachment(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(library_search.config, "load", return_value={}), \
                patch.object(library_search.chat_models, "engine_cfg", return_value=({"engine": "claude", "claude": {}}, {"name": "fake"})), \
                patch.object(library_search.chat, "stream", return_value=iter(["image answer"])) as stream:
            result = library_search.answer(SimpleNamespace(root=Path(tmp)), SimpleNamespace(snapshot=lambda _: ([], 0)), {"images": [picture()]})
            self.assertEqual(result["answer"], "image answer")
            self.assertEqual(len(stream.call_args.kwargs["images"]), 1)

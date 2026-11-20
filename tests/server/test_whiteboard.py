"""Opt-in whiteboard HTTP/storage contract, with no real library or model."""
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import base64
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request

from easyread.library import whiteboard
from easyread.server.server import Handler
from tests.http_support import anonymous_access


class WhiteboardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.workspaces = {}
        for pid in ("paper", "other"):
            folder = self.root / pid
            folder.mkdir()
            self.workspaces[pid] = SimpleNamespace(root=folder, id=pid)
        app = SimpleNamespace(token="test-token", access=anonymous_access(self.root),
                              lib=SimpleNamespace(root=self.root, ws=self.workspaces.get),
                              location=SimpleNamespace(request=lambda *args: nullcontext(), status="idle"))
        handler = type("WhiteboardHandler", (Handler,), {"app": app, "log_message": lambda *args: None})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}"
        self.scope = self.request()[1]["library_id"]

    def request(self, body=None, pid="paper", token="test-token", scope=None, suffix=""):
        data = None if body is None else json.dumps({"library_id": scope or getattr(self, "scope", ""), **body}).encode()
        req = urllib.request.Request(self.url + "/api/p/" + pid + "/whiteboard" + suffix, data=data,
                                     headers={"Content-Type": "application/json", "X-Token": token})
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, response.read() if response.headers.get_content_type() == "image/png" else json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def create(self):
        code, result = self.request({"action": "create"})
        self.assertEqual(code, 200)
        return result["board"]

    def test_read_does_not_allocate_and_save_cannot_create(self):
        self.assertIsNone(self.request()[1]["board"])
        self.assertEqual(list(self.workspaces["paper"].root.iterdir()), [])
        code, _ = self.request({"action": "save", "revision": "missing", "mutation": "mutation1", "scene": {"elements": []}})
        self.assertEqual(code, 409)
        self.assertFalse((self.workspaces["paper"].root / "whiteboard.json").exists())

    def test_create_idempotence_and_paper_isolation(self):
        board = self.create()
        self.assertEqual(self.create(), board)
        self.assertIsNone(self.request(pid="other")[1]["board"])
        self.assertEqual(self.request(pid="missing")[0], 404)
        self.assertEqual(self.request(pid="%2E%2E")[0], 404)

    def test_files_library_revision_and_lost_receipt(self):
        board = self.create()
        scene = board["scene"]
        scene["elements"] = [{"id": "r", "type": "rectangle", "x": 10, "link": "/read/paper#b-p1"}]
        scene["files"] = {"image": {"id": "image", "mimeType": "image/png", "dataURL": "data:image/png;base64,aGVsbG8="}}
        scene["libraryItems"] = [{"id": "lib", "elements": [{"id": "s", "type": "ellipse"}]}]
        body = {"action": "save", "revision": board["revision"], "mutation": "mutation-1", "scene": scene}
        code, result = self.request(body)
        self.assertEqual(code, 200)
        self.assertNotEqual(result["revision"], board["revision"])
        self.assertEqual(self.request(body), (200, result))
        body["mutation"] = "mutation-2"
        self.assertEqual(self.request(body)[0], 409)
        self.assertEqual(self.request()[1]["board"]["scene"], scene)

    def test_competing_writes_only_one_wins(self):
        board = self.create()
        bodies = [{"action": "save", "revision": board["revision"], "mutation": "mutation-" + str(i), "scene": board["scene"]} for i in range(2)]
        with ThreadPoolExecutor(2) as pool:
            codes = sorted(result[0] for result in pool.map(lambda body: whiteboard.update(self.workspaces["paper"], body), bodies))
        self.assertEqual(codes, [200, 409])

    def test_bad_token_and_library_identity_never_create(self):
        self.assertEqual(self.request({"action": "create"}, token="wrong")[0], 403)
        self.assertEqual(self.request({"action": "create"}, scope="another-library")[0], 409)
        self.assertIsNone(self.request()[1]["board"])

    def test_invalid_scene_preserves_saved_board(self):
        board = self.create()
        scenes = [{"elements": "wrong"}, {"elements": [{"id": "x", "type": "rectangle", "link": "javascript:alert(1)"}]},
                  {"elements": [{"id": "x", "type": "embeddable"}]}, {"elements": [], "appState": []},
                  {"elements": [], "files": {"x": {"id": "x", "dataURL": "https://example.test/image.png"}}},
                  {"elements": [], "libraryItems": [{"elements": [{"id": "a", "type": "iframe"}]}]},
                  {"elements": [], "appState": {"scrollX": float("nan")}}]
        for scene in scenes:
            with self.subTest(scene=scene):
                body = {"action": "save", "revision": board["revision"], "mutation": "mutation-1", "scene": scene}
                self.assertEqual(self.request(body)[0], 400)
                self.assertEqual(self.request()[1]["board"], board)
        path = self.workspaces["paper"].root / "whiteboard.json"
        path.write_text('{broken', encoding="utf-8")
        self.assertEqual(self.request()[0], 409)
        self.assertEqual(self.request({"action": "create"})[0], 400)
        self.assertEqual(path.read_text(), '{broken')

    def test_scene_size_limit_and_private_transient_state(self):
        with self.assertRaises(ValueError):
            whiteboard.validate({"elements": [], "padding": "x" * whiteboard.MAX_BYTES})
        scene = whiteboard.validate({"elements": [], "appState": {"viewBackgroundColor": "#fff", "collaborators": {}, "fileHandle": "secret"}})
        self.assertEqual(scene["appState"], {"viewBackgroundColor": "#fff"})

    def png(self):
        from PIL import Image
        output = io.BytesIO()
        Image.new("RGB", (32, 24), "white").save(output, format="PNG")
        return output.getvalue()

    def test_preview_is_versioned_validated_and_removed_with_board(self):
        self.assertEqual(self.request(suffix="/image")[0], 404)
        board = self.create()
        self.assertEqual(self.request()[1]["preview"]["status"], "pending")
        self.assertEqual(self.request(suffix="/image")[0], 409)
        body = {"action": "preview", "revision": board["revision"], "image": base64.b64encode(self.png()).decode()}
        self.assertEqual(self.request(body)[0], 200)
        self.assertEqual(self.request(suffix="/image?revision=" + board["revision"]), (200, self.png()))
        self.assertEqual(self.request()[1]["preview"]["status"], "ready")
        invalid = {**body, "image": base64.b64encode(b"<svg/>").decode()}
        self.assertEqual(self.request(invalid)[0], 400)
        code, saved = self.request({"action": "save", "revision": board["revision"], "mutation": "new-version", "scene": board["scene"]})
        self.assertEqual(code, 200)
        self.assertEqual(self.request(suffix="/image")[0], 409)
        self.assertEqual(self.request(body)[0], 409)
        self.assertEqual(self.request({**body, "revision": saved["revision"]})[0], 200)
        self.assertEqual(len(list(self.workspaces["paper"].root.glob("whiteboard-preview-*.png"))), 1)
        self.assertEqual(self.request(suffix="/image?revision=" + board["revision"])[0], 409)
        self.assertEqual(self.request({"action": "delete", "revision": saved["revision"]})[0], 200)
        self.assertIsNone(self.request()[1]["board"])
        self.assertEqual(list(self.workspaces["paper"].root.glob("whiteboard*")), [])

    def test_delete_checks_revision_and_delayed_writes_cannot_resurrect(self):
        board = self.create()
        self.assertEqual(self.request({"action": "delete", "revision": "stale"})[0], 409)
        self.assertEqual(self.request({"action": "delete", "revision": board["revision"]}, token="wrong")[0], 403)
        self.assertEqual(self.request({"action": "delete"})[0], 400)
        self.assertEqual(self.request({"action": "delete", "revision": board["revision"]})[0], 200)
        self.assertEqual(self.request({"action": "delete", "revision": board["revision"]})[0], 200)
        late = {"action": "save", "revision": board["revision"], "mutation": "late-save", "scene": board["scene"]}
        self.assertEqual(self.request(late)[0], 409)
        preview = {"action": "preview", "revision": board["revision"], "image": base64.b64encode(self.png()).decode()}
        self.assertEqual(self.request(preview)[0], 409)
        new = self.create()
        self.assertNotEqual(new["revision"], board["revision"])
        self.assertEqual(self.request(late)[0], 409)
        self.assertEqual(self.request({"action": "delete", "revision": board["revision"]})[0], 409)
        self.assertEqual(self.request()[1]["board"], new)

    def test_pwa_does_not_precache_editor_fonts(self):
        from easyread.server.pwa import worker
        script = worker()
        assets = json.loads(script.split("const ASSETS = ", 1)[1].split(";", 1)[0])
        self.assertFalse(any("/vendor/whiteboard/" in path for path in assets))
        self.assertIn("/web/js/reader/whiteboard.js", assets)


if __name__ == "__main__":
    unittest.main()

"""Small boundary smoke suite; no real models, downloads or user's library."""
import asyncio
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from easyread.app import config
from easyread.library.store import write_json_atomic
from easyread.server import launch, server, public_network
from easyread.server.public_schema import document


class PublicAPITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"EASYREAD_LIBRARY": self.temp.name, "EASYREAD_ACCESS_CODE": "alpha-code",
                                          "EASYREAD_OPEN_API": "1", "EASYREAD_MCP": "1"})
        self.env.start()
        self.jobs = patch("easyread.server.server.Jobs")
        self.jobs.start()
        self.app = server.App(config.load())
        self.app_patch = patch.object(server.Handler, "app", self.app, create=True)
        self.app_patch.start()
        self.httpd = launch._Server(("127.0.0.1", 0), server.Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.app.public_api.base_url = self.base
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.ws, _ = self.app.lib.create_from_pdf(b"%PDF-test fixture", "sample.pdf")
        self.ws.update("paper", lambda p: p.update(blocks=[
            {"id": "p1", "type": "para", "page": 1, "en": "Original", "zh": "Translation"},
            {"id": "p2", "type": "para", "page": 1, "en": "Second"}]))
        self.ws.update("reader", lambda r: r.update(edits={"p1": {"zh": "Revised"}}, paper_note={"body": "Whole paper"}, notes={
            "n1": {"id": "n1", "kind": "note", "anchor": "p1", "body": "Note"},
            "h1": {"id": "h1", "kind": "highlight", "anchor": "p1", "quote": "Original", "color": "yellow"},
            "n2": {"id": "n2", "kind": "note", "deleted": True}}))
        self.ws.patch_item({"tags": ["Parent/Child"]})

    def tearDown(self):
        self.app.public_api.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app_patch.stop()
        self.jobs.stop()
        self.env.stop()
        self.temp.cleanup()

    def request(self, method, path, data=None, auth="alpha-code", headers=None):
        h = {"Accept": "application/json, text/event-stream"}
        if auth is not None:
            h["Authorization"] = "Bearer " + auth
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        try:
            conn.request(method, path, data, h)
            r = conn.getresponse()
            body = r.read()
            return r.status, json.loads(body) if body else None
        finally:
            conn.close()

    def test_auth_toggle_and_legacy_isolation(self):
        for path in ("/api/v1/info", "/mcp"):
            self.assertEqual(self.request("GET", path, auth=None)[0], 401)
            self.assertEqual(self.request("GET", path, auth="wrong")[0], 401)
        self.assertEqual(self.request("GET", "/api/library")[0], 401)  # Bearer doesn't unlock internal APIs.
        self.assertEqual(self.request("GET", "/api/v1/info")[0], 200)
        self.app.access.update({"enabled": False, "current_code": "alpha-code"})
        self.assertEqual(self.request("GET", "/api/v1/info", auth=None)[0], 200)
        self.assertEqual(self.request("GET", "/mcp", auth=None)[0], 405)  # Auth off, POST-only transport.
        self.assertEqual(self.request("GET", "/api/v1/info", headers={"Origin": "https://evil.example"})[0], 403)
        self.app.public_api.settings["enabled"] = False
        self.assertEqual(self.request("GET", "/api/v1/info")[0], 404)

    def test_reads_pagination_categories_and_effective_content(self):
        versions = self.ws.versions()
        root = "/api/v1/documents/" + self.ws.id
        self.assertEqual(self.request("GET", "/api/v1/documents?limit=1&category=Parent")[1]["total"], 1)
        self.assertEqual(self.request("GET", root)[1]["id"], self.ws.id)
        self.assertEqual(self.request("GET", root + "/notes")[1]["paper_note"]["body"], "Whole paper")
        self.assertEqual(self.request("GET", root + "/notes")[1]["total"], 1)
        self.assertEqual(self.request("GET", root + "/highlights")[1]["items"][0]["color"], "yellow")
        data = self.request("GET", root + "/content?limit=1")[1]
        self.assertEqual(data["items"][0]["zh"], "Revised")
        self.assertEqual(data["next_offset"], 1)
        self.assertEqual(self.request("GET", root + "/content?view=raw")[1]["items"][0]["zh"], "Translation")
        self.assertNotIn("zh", self.request("GET", root + "/content?language=original")[1]["items"][0])
        self.assertTrue(self.request("GET", root + "/links?block=p1")[1]["web_url"].endswith("#p1"))
        with patch("easyread.server.public_api.prefs.load", return_value={"library": {"cats": ["Empty"]}}):
            cats = self.request("GET", "/api/v1/categories")[1]["items"]
        self.assertEqual([x["path"] for x in cats], ["Empty", "Parent", "Parent/Child"])
        self.assertEqual(versions, self.ws.versions())
        self.assertEqual(self.request("GET", root + "/content?limit=0")[0], 400)
        self.assertEqual(self.request("GET", "/api/v1/documents/absent")[0], 404)
        self.assertEqual(self.request("GET", "/api/v1/openapi.json")[1]["openapi"], "3.1.0")
        self.assertIn("/imports/file", document()["paths"])

    def test_whiteboard_preview_auth_and_document_lifecycle(self):
        import base64
        import io
        from PIL import Image
        from easyread.library import whiteboard, trash
        root = "/api/v1/documents/" + self.ws.id + "/whiteboard"
        self.assertIsNone(self.request("GET", root)[1]["board"])
        self.assertEqual(self.request("GET", root + "/image")[0], 404)
        self.assertFalse((self.ws.root / "whiteboard.json").exists())
        _, result = whiteboard.update(self.ws, {"action": "create"})
        revision = result["board"]["revision"]
        output = io.BytesIO()
        Image.new("RGB", (24, 16), "white").save(output, format="PNG")
        png = output.getvalue()
        whiteboard.update(self.ws, {"action": "preview", "revision": revision, "image": base64.b64encode(png).decode()})
        info = self.request("GET", root)[1]
        self.assertEqual(info["preview"]["status"], "ready")
        self.assertTrue(info["preview"]["url"].endswith("?revision=" + revision))
        self.assertEqual(self.request("GET", root, auth=None)[0], 401)
        self.assertEqual(self.request("GET", root + "/image", auth=None)[0], 401)
        self.assertEqual(self.request("GET", root + "/image?revision=old")[0], 409)
        self.assertEqual(self.request("POST", root, {"action": "delete"})[0], 405)
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        try:
            conn.request("GET", root + "/image?revision=" + revision, headers={"Authorization": "Bearer alpha-code"})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.read(), png)
        finally:
            conn.close()
        reader = self.ws.load("reader")
        deleted = self.app.lib.trash(self.ws.id)
        self.assertTrue((deleted / "whiteboard.json").exists())
        self.assertEqual(len(list(deleted.glob("whiteboard-preview-*.png"))), 1)
        self.assertEqual(self.request("GET", root)[0], 404)
        self.assertEqual(self.request("GET", root + "/image")[0], 404)
        trash.restore(self.app.lib.root, deleted.name)
        self.assertEqual(self.request("GET", root)[1]["board"]["revision"], revision)
        self.assertEqual(self.ws.load("reader"), reader)
        self.assertEqual(whiteboard.preview(self.ws, revision), (200, png))
        deleted = self.app.lib.trash(self.ws.id)
        trash.purge(self.app.lib.root, deleted.name)
        self.assertFalse(deleted.exists())
        self.assertEqual(self.request("GET", root)[0], 404)
        self.assertIn("/documents/{id}/whiteboard/image", document()["paths"])

    def wait_task(self, tid):
        for _ in range(100):
            result = self.request("GET", "/api/v1/tasks/" + tid)[1]
            if result["state"] not in ("queued", "running"):
                return result
            time.sleep(0.02)
        self.fail("Import didn't finish")

    def test_pdf_import_and_duplicate(self):
        data = b"%PDF-test fixture"
        for _ in range(2):
            code, result = self.request("POST", "/api/v1/imports/file?translate=false", data, headers={"Content-Type": "application/pdf"})
            self.assertEqual(code, 202)
            task = self.wait_task(result["task_id"])
            self.assertEqual(task["state"], "done")
            self.assertEqual(task["document_id"], self.ws.id)
            self.assertFalse(task["new"])
        self.assertEqual(self.app.location.active, 0)
        self.assertEqual(self.request("POST", "/api/v1/imports/file", b"bad", headers={"Content-Type": "application/pdf"})[0], 400)
        self.assertEqual(self.request("POST", "/api/v1/imports/file", b"", headers={"Content-Type": "application/pdf", "Content-Length": str(51 * 1024 * 1024)})[0], 413)

    def test_url_queue_and_safety(self):
        for url in ("file:///etc/passwd", "http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/", "http://user:password@example.org/"):
            self.assertEqual(self.request("POST", "/api/v1/imports/url", {"url": url})[0], 400)
        with patch.object(self.app.lib, "import_reference", return_value=(self.ws, False)):
            code, result = self.request("POST", "/api/v1/imports/url", {"url": "https://8.8.8.8/sample.pdf"})
            self.assertEqual(code, 202)
            self.assertEqual(self.wait_task(result["task_id"])["state"], "done")
        with public_network.importing():
            with self.assertRaises(ValueError):
                public_network.urlopen("http://127.0.0.1/")
            from easyread.weblog.snapshot import render
            with self.assertRaises(ValueError):
                render("https://example.org")

    def test_private_html_import_offline_dedup_and_short_text(self):
        payload = {"html": '<html><body><script>fetch("http://127.0.0.1/")</script><style>@import "http://127.0.0.1/";</style><p onclick="evil()">短篇中文笔记</p><img src="http://127.0.0.1/private"><svg onload="evil()"></svg></body></html>',
                   "title": "Private note", "source_id": "siyuan:test", "translate": False}
        self.assertEqual(self.request("POST", "/api/v1/imports/html", payload, auth=None)[0], 401)
        self.assertIn("import_html", self.request("GET", "/api/v1/info")[1]["capabilities"])
        with patch("easyread.pdf.sources._get", side_effect=AssertionError("must not fetch")), \
             patch("easyread.weblog.cover.ensure", side_effect=AssertionError("must not render")):
            ids = []
            for n in range(3):
                if n == 2:
                    payload["html"] += "<p>new revision</p>"
                status, accepted = self.request("POST", "/api/v1/imports/html", payload)
                self.assertEqual(status, 202)
                task = self.wait_task(accepted["task_id"])
                self.assertEqual(task["state"], "done", task)
                self.assertFalse(task["queued"])
                ids.append(task["document_id"])
                if n == 0:
                    self.app.lib.ws(ids[0]).update("reader", lambda r: r.update(paper_note={"body": "Keep my note"}))
            self.assertEqual(self.app.lib.ws(ids[1]).load("reader")["paper_note"]["body"], "Keep my note")
            self.assertEqual(ids[0], ids[1])
            self.assertNotEqual(ids[0], ids[2])
            ws = self.app.lib.ws(ids[0])
            source = (ws.root / "source.html").read_text()
            self.assertNotIn("onclick", source)
            self.assertNotIn("<script", source)
            self.assertNotIn("<svg", source)
            self.assertNotIn("127.0.0.1", source)
            self.assertIn("img-src data:", source)
            content = self.request("GET", f"/api/v1/documents/{ids[0]}/content")[1]
            self.assertIn("短篇中文笔记", " ".join(b.get("en", "") for b in content["items"]))
            self.app.jobs.enqueue.assert_not_called()
            self.assertIn("/imports/html", document()["paths"])

    def test_private_html_validation(self):
        for payload in ({"html": ""}, {"html": 1}, {"html": "<p>ok</p>", "source_id": []},
                        {"html": "<p>ok</p>", "url": "http://127.0.0.1"},
                        {"html": "<p>ok</p>", "translate": "true"}, {"html": "<p>ok</p>", "title": "x" * 501}):
            self.assertEqual(self.request("POST", "/api/v1/imports/html", payload)[0], 400)
        self.assertEqual(self.request("POST", "/api/v1/imports/html", b"", headers={"Content-Type": "application/json", "Content-Length": str(13 * 1024 * 1024)})[0], 413)
        from easyread.weblog.upload import prepare
        with self.assertRaises(ValueError):
            prepare("<html><body><script>only active content</script></body></html>")
        html, _, _ = prepare('<p>image note</p><img src="data:image/png;base64,aGk="><img src="data:image/svg+xml;base64,PHN2Zz4=">')
        self.assertIn("data:image/png;base64,aGk=", html)
        self.assertNotIn("data:image/svg", html)

    def test_mcp_sdk_handshake_list_and_read(self):
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            import httpx
        except ImportError:
            self.skipTest("Install .[mcp] for MCP smoke test")
        async def check():
            async with httpx.AsyncClient(headers={"Authorization": "Bearer alpha-code"}) as http:
                async with streamable_http_client(self.base + "/mcp", http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as client:
                        info = await client.initialize()
                        self.assertEqual(info.serverInfo.name, "EasyRead")
                        tools = await client.list_tools()
                        self.assertEqual(len(tools.tools), 10)
                        result = await client.call_tool("get_content", {"document_id": self.ws.id, "limit": 1})
                        self.assertFalse(result.isError)
                        self.assertEqual(json.loads(result.content[0].text)["items"][0]["zh"], "Revised")
                        missing = await client.call_tool("get_document", {"document_id": "absent"})
                        self.assertTrue(missing.isError)
        asyncio.run(check())

import gzip
import http.client
import io
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from easyread.library.library import Library
from easyread.server import server, static_files
from tests.http_support import anonymous_access


class StaticFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.web = self.root / "web"
        self.web.mkdir()
        self.script = self.web / "app.js"
        self.script.write_bytes(b"const example = 'static asset';\n" * 100)
        (self.web / "vendor").mkdir()
        (self.web / "vendor" / "test.js").write_bytes(b"// vendor\n" * 200)
        (self.web / "library.html").write_text("<html><head></head><body>" + "Library " * 200 + "</body></html>")
        app = SimpleNamespace(
            lib=Library(self.root / "library"), access=anonymous_access(self.root), token="test-token",
            jobs=SimpleNamespace(small_status=lambda: []),
            location=SimpleNamespace(request=lambda *args: nullcontext(), status="idle",
                                     marker=SimpleNamespace(other_device=lambda: False)))

        class Handler(server.Handler):
            def log_message(self, *args):
                pass

        Handler.app = app
        self.patches = [patch.object(static_files, "_cache", static_files._RepresentationCache()),
                        patch.object(server, "WEB", self.web), patch.object(server.config, "load", return_value={}),
                        patch.object(server.config, "is_first_run", return_value=False)]
        for p in self.patches:
            p.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)

    def tearDown(self):
        self.conn.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def request(self, path="/web/app.js", method="GET", headers=None):
        self.conn.request(method, path, headers=headers or {})
        response = self.conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_get_conditional_and_keepalive_have_correct_framing(self):
        code, headers, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(body, self.script.read_bytes())
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertIn("javascript", headers["Content-Type"])
        self.assertEqual(headers["Cache-Control"], "private, no-cache")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Vary"], "Accept-Encoding")
        etag = headers["ETag"]
        for validator in (etag, etag.removeprefix("W/"), '"other", ' + etag, "*"):
            with self.subTest(validator=validator):
                code, headers, body = self.request(headers={"If-None-Match": validator})
                self.assertEqual((code, body), (304, b""))
                self.assertEqual(headers["ETag"], etag)
                self.assertEqual(headers["Cache-Control"], "private, no-cache")
                self.assertEqual(headers["Vary"], "Accept-Encoding")
                self.assertNotIn("Content-Length", headers)
        self.assertEqual(self.request()[2], self.script.read_bytes())
        self.assertEqual(self.request(headers={"If-None-Match": '"unrelated"'})[0], 200)

    def test_head_has_get_headers_no_body_and_can_revalidate(self):
        _, get, _ = self.request()
        code, head, body = self.request(method="HEAD")
        self.assertEqual((code, body), (200, b""))
        for key in ("ETag", "Content-Length", "Content-Type", "Cache-Control"):
            self.assertEqual(head[key], get[key])
        self.assertEqual(self.request(method="HEAD", headers={"If-None-Match": head["ETag"]})[0], 304)
        self.assertEqual(self.request()[0], 200)

    def test_file_replacement_changes_validator_even_with_same_mtime_and_size(self):
        _, headers, _ = self.request()
        previous = self.script.stat()
        replacement = self.web / "replacement.js"
        replacement.write_bytes(self.script.read_bytes().replace(b"example", b"changed"))
        os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        replacement.replace(self.script)
        code, fresh, body = self.request(headers={"If-None-Match": headers["ETag"]})
        self.assertEqual(code, 200)
        self.assertNotEqual(fresh["ETag"], headers["ETag"])
        self.assertIn(b"changed", body)

    def test_vendor_and_query_paths_revalidate_instead_of_being_immutable(self):
        for path in ("/web/vendor/test.js", "/web/app.js?v=2"):
            code, headers, _ = self.request(path, headers={"Accept-Encoding": "gzip"})
            self.assertEqual(code, 200)
            self.assertEqual(headers["Content-Encoding"], "gzip")
            self.assertEqual(headers["Cache-Control"], "private, no-cache")
            self.assertEqual(self.request(path, headers={"Accept-Encoding": "gzip", "If-None-Match": headers["ETag"]})[0], 304)

    def test_missing_and_traversal_remain_uncacheable(self):
        (self.root / "secret.txt").write_text("private")
        for path in ("/web/missing.js", "/web/../secret.txt", "/web/%2e%2e/secret.txt"):
            code, headers, body = self.request(path, headers={"Accept-Encoding": "gzip", "If-None-Match": "*"})
            self.assertEqual(code, 404)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertNotIn("ETag", headers)
            self.assertNotIn("Content-Encoding", headers)
            self.assertNotIn(b"private", body)

    def test_api_and_injected_pages_keep_no_store_and_trash_uses_count(self):
        with patch.object(server.trash, "items", side_effect=AssertionError("trash metadata read")), \
                patch.object(server.trash, "count", return_value=3) as count:
            code, headers, body = self.request("/api/library", headers={"Accept-Encoding": "gzip", "If-None-Match": "*"})
        self.assertEqual(code, 200)
        self.assertIn(b'"trash": 3', body)
        count.assert_called_once()
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("ETag", headers)
        self.assertNotIn("Content-Encoding", headers)
        code, headers, _ = self.request("/", headers={"Accept-Encoding": "gzip", "If-None-Match": "*"})
        self.assertEqual(code, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("ETag", headers)
        self.assertNotIn("Content-Encoding", headers)

    def test_gzip_roundtrip_and_accept_encoding_negotiation(self):
        original = self.script.read_bytes()
        identity_etag = self.request()[1]["ETag"]
        cases = (
            (None, False), ("", False), ("identity", False), ("br", False),
            ("gzip", True), ("br, gzip;q=0.5", True), ("GZip;Q=1.000", True),
            ("*", True), ("*;q=0.5", True), ("*;q=0, gzip;q=1", True),
            ("gzip;q=0", False), ("gzip;q=0, *;q=1", False), ("*;q=0", False),
            ("identity;q=1, gzip;q=0.5", False), ("identity;q=0.1, gzip;q=0.5", True),
            ("identity;q=0, gzip;q=0, *;q=0", False),
            ("gzip, gzip;q=0", False), ("gzip;q=NaN", False), ("gzip;q=2", False),
            ("gzip;q=bogus, *;q=1", False), ("gzip;q=-1", False),
        )
        for value, compressed in cases:
            with self.subTest(value=value):
                code, headers, body = self.request(headers={} if value is None else {"Accept-Encoding": value})
                self.assertEqual(code, 200)
                self.assertEqual(headers.get("Content-Encoding"), "gzip" if compressed else None)
                self.assertEqual(gzip.decompress(body) if compressed else body, original)
                self.assertEqual(int(headers["Content-Length"]), len(body))
                self.assertEqual(headers["Vary"], "Accept-Encoding")
                if compressed:
                    self.assertLess(len(body), len(original))
                    self.assertNotEqual(headers["ETag"], identity_etag)
                else:
                    self.assertEqual(headers["ETag"], identity_etag)

    def test_repeated_accept_encoding_fields_respect_explicit_refusal(self):
        self.conn.putrequest("GET", "/web/app.js", skip_accept_encoding=True)
        self.conn.putheader("Accept-Encoding", "*")
        self.conn.putheader("Accept-Encoding", "gzip;q=0")
        self.conn.endheaders()
        response = self.conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIsNone(response.getheader("Content-Encoding"))
        self.assertEqual(response.read(), self.script.read_bytes())

    def test_gzip_variant_revalidation_and_identity_do_not_cross_match(self):
        _, identity, _ = self.request()
        _, compressed, _ = self.request(headers={"Accept-Encoding": "gzip"})
        for validator in (compressed["ETag"], compressed["ETag"].removeprefix("W/"),
                          '"other", ' + compressed["ETag"], "*"):
            for method in ("GET", "HEAD"):
                with self.subTest(validator=validator, method=method):
                    code, headers, body = self.request(method=method, headers={"Accept-Encoding": "gzip", "If-None-Match": validator})
                    self.assertEqual((code, body), (304, b""))
                    self.assertEqual(headers["ETag"], compressed["ETag"])
                    self.assertEqual(headers["Content-Encoding"], "gzip")
                    self.assertEqual(headers["Vary"], "Accept-Encoding")
                    self.assertNotIn("Content-Length", headers)
        code, headers, body = self.request(headers={"Accept-Encoding": "gzip", "If-None-Match": identity["ETag"]})
        self.assertEqual(code, 200)
        self.assertEqual(gzip.decompress(body), self.script.read_bytes())
        for value in ("identity", "gzip;q=0, *;q=1"):
            code, headers, body = self.request(headers={"Accept-Encoding": value, "If-None-Match": compressed["ETag"]})
            self.assertEqual(code, 200)
            self.assertEqual(headers["ETag"], identity["ETag"])
            self.assertEqual(body, self.script.read_bytes())

    def test_cold_head_is_identity_and_warm_head_describes_gzip(self):
        code, cold, body = self.request(method="HEAD", headers={"Accept-Encoding": "gzip"})
        self.assertEqual((code, body), (200, b""))
        self.assertNotIn("Content-Encoding", cold)
        self.assertEqual(int(cold["Content-Length"]), self.script.stat().st_size)
        self.assertEqual(cold["Vary"], "Accept-Encoding")
        _, get, _ = self.request(headers={"Accept-Encoding": "gzip"})
        code, head, body = self.request(method="HEAD", headers={"Accept-Encoding": "gzip"})
        self.assertEqual((code, body), (200, b""))
        for key in ("ETag", "Content-Encoding", "Content-Length", "Content-Type", "Cache-Control", "Vary"):
            self.assertEqual(head[key], get[key])

    def test_gzip_cache_observes_atomic_replacement_and_deletion(self):
        _, headers, old = self.request(headers={"Accept-Encoding": "gzip"})
        previous = self.script.stat()
        replacement = self.web / "replacement.js"
        replacement.write_bytes(self.script.read_bytes().replace(b"example", b"changed"))
        os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        replacement.replace(self.script)
        code, fresh, body = self.request(headers={"Accept-Encoding": "gzip", "If-None-Match": headers["ETag"]})
        self.assertEqual(code, 200)
        self.assertNotEqual(fresh["ETag"], headers["ETag"])
        self.assertNotEqual(body, old)
        self.assertEqual(gzip.decompress(body), self.script.read_bytes())
        self.assertEqual(len(static_files._cache.entries), 1)
        self.script.unlink()
        code, missing, _ = self.request(headers={"Accept-Encoding": "gzip", "If-None-Match": fresh["ETag"]})
        self.assertEqual(code, 404)
        self.assertEqual(missing["Cache-Control"], "no-store")
        self.assertNotIn("Content-Encoding", missing)
        self.assertNotIn("ETag", missing)
        # _safe rejects missing paths before send; no stale cache entry can be served.
        self.script.write_bytes(b"const restored = 'new version';\n" * 100)
        code, restored, body = self.request(headers={"Accept-Encoding": "gzip", "If-None-Match": fresh["ETag"]})
        self.assertEqual(code, 200)
        self.assertNotEqual(restored["ETag"], fresh["ETag"])
        self.assertEqual(gzip.decompress(body), self.script.read_bytes())

    def test_only_appropriate_packaged_types_and_sizes_are_compressed(self):
        for suffix in ("js", "mjs", "css", "json", "svg", "txt", "html", "pdf", "png", "jpg", "woff", "woff2", "wasm"):
            with self.subTest(suffix=suffix):
                asset = self.web / ("asset." + suffix)
                asset.write_bytes(b"packaged asset content\n" * 100)
                code, headers, body = self.request("/web/" + asset.name, headers={"Accept-Encoding": "gzip"})
                self.assertEqual(code, 200)
                if suffix in ("js", "mjs", "css", "json", "svg", "txt"):
                    self.assertEqual(headers["Content-Encoding"], "gzip")
                    self.assertEqual(gzip.decompress(body), asset.read_bytes())
                else:
                    self.assertNotIn("Content-Encoding", headers)
                    self.assertEqual(body, asset.read_bytes())
        for size, compressed in ((static_files._MIN_GZIP_SIZE - 1, False),
                                 (static_files._MIN_GZIP_SIZE, True),
                                 (static_files._MAX_GZIP_SIZE, True),
                                 (static_files._MAX_GZIP_SIZE + 1, False)):
            with self.subTest(size=size):
                self.script.write_bytes(b"a" * size)
                code, headers, body = self.request(headers={"Accept-Encoding": "gzip"})
                self.assertEqual(code, 200)
                self.assertEqual(headers.get("Content-Encoding"), "gzip" if compressed else None)
                self.assertEqual(len(gzip.decompress(body) if compressed else body), size)

    def test_range_request_keeps_existing_identity_response(self):
        self.request(headers={"Accept-Encoding": "gzip"})
        code, headers, body = self.request(headers={"Accept-Encoding": "gzip", "Range": "bytes=0-9"})
        self.assertEqual(code, 200)
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body, self.script.read_bytes())


class StaticFilesCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.script = self.root / "app.js"
        self.original = b"const example = 'static asset';\n" * 100
        self.script.write_bytes(self.original)
        self.cache = static_files._RepresentationCache()
        patcher = patch.object(static_files, "_cache", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, *, method="GET", headers=None, path=None, forbid_read=False):
        path = path or self.script
        handler = SimpleNamespace(headers=headers or {}, command=method, close_connection=False,
                                  send_response=Mock(), send_header=Mock(), end_headers=Mock(),
                                  wfile=io.BytesIO(), _json=Mock())
        if forbid_read:
            with path.open("rb") as stream:
                handle = MagicMock(wraps=stream)
                handle.__enter__.return_value = handle
                handle.read.side_effect = AssertionError("body read")
                with patch.object(Path, "open", return_value=handle):
                    static_files.send(handler, path)
        else:
            static_files.send(handler, path)
        status = handler.send_response.call_args.args[0] if handler.send_response.called else None
        headers = dict(call.args for call in handler.send_header.call_args_list)
        return status, headers, handler.wfile.getvalue()

    def test_head_and_304_never_read_or_compress_cold_or_warm_bodies(self):
        identity_etag = self.send()[1]["ETag"]
        gzip_etag = self.send(headers={"Accept-Encoding": "gzip"})[1]["ETag"]
        for warm in (True, False):
            if not warm:
                self.cache.entries.clear()
                self.cache.total_bytes = 0
            for method, accept, validator in (
                ("HEAD", "identity", ""), ("HEAD", "gzip", ""),
                ("GET", "identity", identity_etag), ("HEAD", "identity", identity_etag),
                ("GET", "gzip", gzip_etag), ("HEAD", "gzip", gzip_etag),
                ("GET", "gzip", "*"), ("HEAD", "gzip", "*"),
            ):
                with self.subTest(warm=warm, method=method, accept=accept, validator=validator), \
                        patch.object(static_files.gzip, "compress", side_effect=AssertionError("compression")):
                    code, headers, body = self.send(method=method, forbid_read=True,
                                                   headers={"Accept-Encoding": accept, "If-None-Match": validator})
                    self.assertEqual(code, 304 if validator else 200)
                    self.assertEqual(body, b"")
                    self.assertEqual(headers["Vary"], "Accept-Encoding")
                    if code == 304:
                        self.assertNotIn("Content-Length", headers)
                    if validator == gzip_etag:
                        self.assertEqual(headers["Content-Encoding"], "gzip")
                        self.assertEqual(headers["ETag"], gzip_etag)

        code, headers, body = self.send(headers={"Accept-Encoding": "gzip", "If-None-Match": identity_etag}, forbid_read=True)
        self.assertEqual((code, body), (304, b""))
        self.assertEqual(headers["ETag"], identity_etag)

    def test_evicted_gzip_validator_requires_current_eligibility_and_negotiation(self):
        gzip_etag = self.send(headers={"Accept-Encoding": "gzip"})[1]["ETag"]
        self.cache.entries.clear()
        self.cache.total_bytes = 0
        for extra in ({"Accept-Encoding": "gzip;q=0"}, {},
                      {"Accept-Encoding": "gzip", "Range": "bytes=0-9"}):
            with self.subTest(extra=extra):
                code, headers, body = self.send(headers={"If-None-Match": gzip_etag, **extra})
                self.assertEqual(code, 200)
                self.assertNotIn("Content-Encoding", headers)
                self.assertEqual(body, self.original)
        with patch.object(static_files, "_MAX_GZIP_SIZE", 1):
            code, headers, body = self.send(headers={"Accept-Encoding": "gzip", "If-None-Match": gzip_etag})
        self.assertEqual(code, 200)
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body, self.original)

    def test_large_head_and_conditional_never_read_or_compress(self):
        self.script.write_bytes(b"x" * (static_files._MAX_GZIP_SIZE + 1))
        etag = static_files._etag(self.script.stat())
        with patch.object(static_files.gzip, "compress", side_effect=AssertionError("compression")):
            code, headers, body = self.send(method="HEAD", headers={"Accept-Encoding": "gzip"}, forbid_read=True)
            self.assertEqual((code, body), (200, b""))
            self.assertEqual(int(headers["Content-Length"]), self.script.stat().st_size)
            code, headers, body = self.send(headers={"Accept-Encoding": "gzip", "If-None-Match": etag}, forbid_read=True)
            self.assertEqual((code, body), (304, b""))

    def test_unprofitable_gzip_is_identity_and_negative_decision_is_cached(self):
        import random
        original = random.Random(0).randbytes(4096)
        self.script.write_bytes(original)
        with patch.object(static_files.gzip, "compress", wraps=gzip.compress) as compress:
            for _ in range(2):
                code, headers, body = self.send(headers={"Accept-Encoding": "gzip"})
                self.assertEqual(code, 200)
                self.assertNotIn("Content-Encoding", headers)
                self.assertEqual(body, original)
        compress.assert_called_once()
        self.assertEqual(list(self.cache.entries.values()), [None])
        self.assertEqual(self.cache.total_bytes, 0)

    def test_entry_and_byte_limits_and_lru_eviction(self):
        size = len(gzip.compress(self.original, compresslevel=6, mtime=0))
        for entry_limit, byte_limit in ((2, 10000), (10, 2 * size)):
            with self.subTest(entry_limit=entry_limit, byte_limit=byte_limit):
                cache = static_files._RepresentationCache(entry_limit, byte_limit)
                with patch.object(static_files, "_cache", cache):
                    paths = [self.root / f"asset-{i}.js" for i in range(3)]
                    for path in paths:
                        path.write_bytes(self.original)
                    for path in (paths[0], paths[1], paths[0], paths[2]):
                        self.send(path=path, headers={"Accept-Encoding": "gzip"})
                        self.assertLessEqual(len(cache.entries), entry_limit)
                        self.assertLessEqual(cache.total_bytes, byte_limit)
                        self.assertEqual(cache.total_bytes, sum(len(body or b"") for body in cache.entries.values()))
                    self.assertEqual([key[0] for key in cache.entries], [paths[0], paths[2]])
        # Negative decisions are bounded too, and oversized values are not kept.
        cache = static_files._RepresentationCache(2, size - 1)
        with cache.lock:
            for i in range(3):
                cache.remember((self.root / f"{i}.js", "etag", "gzip"), None)
            cache.remember((self.script, "etag", "gzip"), b"x" * size)
        self.assertEqual(len(cache.entries), 2)
        self.assertEqual(cache.total_bytes, 0)

    def test_identity_request_prunes_old_gzip_and_missing_file_is_not_cached(self):
        self.send(headers={"Accept-Encoding": "gzip"})
        self.script.write_bytes(b"replacement\n" * 100)
        self.send(method="HEAD", forbid_read=True)
        self.assertEqual(len(self.cache.entries), 0)
        self.assertEqual(self.cache.total_bytes, 0)
        self.send(headers={"Accept-Encoding": "gzip"})
        self.script.unlink()
        self.assertEqual(self.send()[0], None)
        self.assertEqual(len(self.cache.entries), 0)
        self.assertEqual(self.cache.total_bytes, 0)

    def test_concurrent_cold_requests_compress_once_and_reuse_identical_bytes(self):
        barrier = threading.Barrier(8)

        def request(_):
            barrier.wait(timeout=5)
            return self.send(headers={"Accept-Encoding": "gzip"})

        with patch.object(static_files.gzip, "compress", wraps=gzip.compress) as compress:
            with ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(pool.map(request, range(8)))
            self.assertEqual(self.send(headers={"Accept-Encoding": "gzip"}), responses[0])
        compress.assert_called_once()
        for code, headers, body in responses:
            self.assertEqual(code, 200)
            self.assertEqual(headers["Content-Encoding"], "gzip")
            self.assertEqual(gzip.decompress(body), self.original)
            self.assertEqual(body, responses[0][2])
        self.assertEqual(len(self.cache.entries), 1)
        self.assertEqual(self.cache.total_bytes, len(responses[0][2]))

    def test_growth_past_compression_bound_during_fstat_stays_uncached_identity(self):
        grown = b"x" * (static_files._MAX_GZIP_SIZE + 100)
        fstat = os.fstat

        def grow_after_stat(fd):
            st = fstat(fd)
            self.script.write_bytes(grown)
            return st

        with patch.object(static_files.os, "fstat", side_effect=grow_after_stat), \
                patch.object(static_files.gzip, "compress", side_effect=AssertionError("compression")):
            code, headers, body = self.send(headers={"Accept-Encoding": "gzip"})
        self.assertEqual(code, 200)
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(int(headers["Content-Length"]), len(grown))
        self.assertEqual(body, grown)
        self.assertFalse(self.cache.entries)

    def test_atomic_replacement_during_fstat_uses_opened_file_bytes(self):
        replacement = self.root / "replacement.js"
        replacement.write_bytes(self.original.replace(b"example", b"changed"))
        fstat = os.fstat

        def replace_after_stat(fd):
            st = fstat(fd)
            replacement.replace(self.script)
            return st

        with patch.object(static_files.os, "fstat", side_effect=replace_after_stat):
            _, old, body = self.send(headers={"Accept-Encoding": "gzip"})
        self.assertEqual(gzip.decompress(body), self.original)
        _, new, body = self.send(headers={"Accept-Encoding": "gzip"})
        self.assertNotEqual(new["ETag"], old["ETag"])
        self.assertEqual(gzip.decompress(body), self.script.read_bytes())
        self.assertEqual(len(self.cache.entries), 1)

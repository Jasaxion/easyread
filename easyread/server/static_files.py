"""Conditional responses for packaged /web assets, not API or paper data."""
from __future__ import annotations

import gzip
import mimetypes
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path


CACHE_CONTROL = "private, no-cache"
_MIN_GZIP_SIZE = 1024
_MAX_GZIP_SIZE = 4 * 1024 * 1024
_GZIP_TYPES = {
    "text/plain", "text/css", "text/javascript", "application/javascript",
    "application/x-javascript", "application/json", "image/svg+xml",
}
_CacheKey = tuple[Path, str, str]


class _RepresentationCache:
    """Bounded LRU; callers hold lock through lookup/read/compress/store."""

    def __init__(self, max_entries: int = 128, max_bytes: int = 8 * 1024 * 1024):
        self.lock = threading.Lock()
        self.entries: OrderedDict[_CacheKey, bytes | None] = OrderedDict()
        self.total_bytes = 0
        self.max_entries = max_entries
        self.max_bytes = max_bytes

    def discard_stale(self, path: Path, etag: str | None) -> None:
        for key in list(self.entries):
            if key[0] == path and key[1] != etag:
                self.total_bytes -= len(self.entries.pop(key) or b"")

    def remember(self, key: _CacheKey, body: bytes | None) -> None:
        # None records an unprofitable compression attempt, without retaining
        # the identity body. Negative decisions count toward the entry limit.
        if self.max_entries < 1 or len(body or b"") > self.max_bytes:
            return
        self.total_bytes -= len(self.entries.pop(key, None) or b"")
        self.entries[key] = body
        self.total_bytes += len(body or b"")
        while len(self.entries) > self.max_entries or self.total_bytes > self.max_bytes:
            _, oldest = self.entries.popitem(last=False)
            self.total_bytes -= len(oldest or b"")


_cache = _RepresentationCache()


def _accepts_gzip(value: str) -> bool:
    qualities: dict[str, float] = {}
    for item in value.lower().split(","):
        coding, *parameters = item.strip().split(";")
        coding = coding.strip()
        quality = 1.0
        if parameters:
            name, _, raw = parameters[0].strip().partition("=")
            quality = (float(raw) if len(parameters) == 1 and name.strip() == "q"
                       and re.fullmatch(r"(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)", raw.strip()) else 0.0)
        # An explicit refusal wins over a wildcard or duplicate token.
        qualities[coding] = min(qualities.get(coding, quality), quality)
    gzip_quality = qualities.get("gzip", qualities.get("*", 0.0))
    return gzip_quality > 0 and gzip_quality >= qualities.get("identity", 0.0)


def _etag(st: os.stat_result) -> str:
    # Metadata validators are weak: they do not claim byte-for-byte identity.
    return 'W/"' + "-".join(format(value, "x") for value in (
        st.st_dev, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size,
    )) + '"'


def _gzip_etag(etag: str) -> str:
    return etag[:-1] + '-gzip"'


def _matches(value: str, etag: str) -> bool:
    return any(tag.strip() == "*" or tag.strip().removeprefix("W/") == etag.removeprefix("W/")
               for tag in value.split(","))


def send(handler, path: Path | None) -> None:
    if path is None:
        return handler._json(404, {"error": "not found"})
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    compressible = ctype in _GZIP_TYPES
    if ctype.startswith("text/") or ctype.endswith("javascript"):
        ctype += "; charset=utf-8"
    # Stat/read the same opened file even on cache hits, so atomic upgrades
    # cannot mix validators from one version with bytes from its replacement.
    try:
        stream = path.open("rb")
    except FileNotFoundError:
        with _cache.lock:
            _cache.discard_stale(path, None)
        return handler._json(404, {"error": "not found"})
    with stream:
        st = os.fstat(stream.fileno())
        etag = _etag(st)
        validator = handler.headers.get("If-None-Match", "")
        accept_encoding = handler.headers.get("Accept-Encoding", "")
        if hasattr(handler.headers, "get_all"):
            accept_encoding = ",".join(handler.headers.get_all("Accept-Encoding", []))
        allow_gzip = (compressible and _MIN_GZIP_SIZE <= st.st_size <= _MAX_GZIP_SIZE
                      and "Range" not in handler.headers and _accepts_gzip(accept_encoding))
        encoding = None
        body = None
        partial_body = False
        with _cache.lock:
            _cache.discard_stale(path, etag)
            key = (path, etag, "gzip")
            if allow_gzip:
                if key in _cache.entries:
                    _cache.entries.move_to_end(key)
                    body = _cache.entries[key]
                    if body is not None:
                        encoding = "gzip"
                elif not _matches(validator, etag):
                    if _matches(validator, _gzip_etag(etag)):
                        # A previous gzip validator remains usable after LRU
                        # eviction, without rereading or recompressing for 304.
                        encoding = "gzip"
                    elif handler.command != "HEAD":
                        body = stream.read(_MAX_GZIP_SIZE + 1)
                        if len(body) == st.st_size:
                            compressed = gzip.compress(body, compresslevel=6, mtime=0)
                            if len(compressed) < len(body):
                                body = compressed
                                encoding = "gzip"
                            _cache.remember(key, body if encoding else None)
                        else:
                            # A concurrent in-place edit may outgrow the bound.
                            # Finish the identity response, but do not cache it.
                            partial_body = True
        if encoding:
            etag = _gzip_etag(etag)
        unchanged = _matches(validator, etag)
        if unchanged or handler.command == "HEAD":
            # Cold HEAD stays identity; warm HEAD describes the cached gzip.
            # Neither HEAD nor 304 ever reads/compresses a body.
            length = len(body) if encoding and body is not None else st.st_size
            body = None
        else:
            if body is None:
                body = stream.read()
            elif partial_body:
                body += stream.read()
            length = len(body)
    handler.send_response(304 if unchanged else 200)
    handler.send_header("ETag", etag)
    # Names (including vendor files) are mutable across upgrades: always
    # revalidate rather than serving an old script alongside new HTML.
    handler.send_header("Cache-Control", CACHE_CONTROL)
    handler.send_header("Vary", "Accept-Encoding")
    if encoding:
        handler.send_header("Content-Encoding", encoding)
    if not unchanged:
        handler.send_header("Content-Type", ctype)
        handler.send_header("Content-Length", str(length))
    if handler.close_connection:
        handler.send_header("Connection", "close")
    handler.end_headers()
    if body is not None:
        handler.wfile.write(body)

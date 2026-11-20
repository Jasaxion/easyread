"""Single-user access code and revocable browser sessions; never HTTP Basic auth."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import tempfile
from http.cookies import SimpleCookie
from pathlib import Path

from ..app.i18n import tr
from ..library.store import read_json

# Optional namespace for isolated previews beside another instance on the same
# host: browser cookies are not scoped to a TCP port. The default is unchanged.
COOKIE = os.environ.get("EASYREAD_ACCESS_COOKIE", "easyread_session")
if not COOKIE or len(COOKIE) > 64 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in COOKIE):
    raise ValueError("Invalid EASYREAD_ACCESS_COOKIE name")
SESSION_AGE = 7 * 86400


class Access:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.sessions = {}
        self.attempts = {}
        self._verification_key = secrets.token_bytes(32)
        self._verified = {}
        self.changed = lambda: None
        missing = object()
        self.state = read_json(path, missing)
        if self.state is missing:
            code = os.environ.get("EASYREAD_ACCESS_CODE", "")
            self.state = self._record(code) if code else {"enabled": False}
            if code:
                self._save(self.state)
        valid = isinstance(self.state, dict) and type(self.state.get("enabled")) is bool
        if valid and self.state["enabled"]:
            try:
                valid = len(bytes.fromhex(self.state["salt"])) == 16 and len(bytes.fromhex(self.state["hash"])) == 32
            except (KeyError, ValueError, TypeError):
                valid = False
        if not valid:
            raise ValueError("Invalid access-code configuration; refusing to start")

    @staticmethod
    def _record(code):
        if not isinstance(code, str) or not 6 <= len(code) <= 256:
            raise ValueError(tr("访问授权码长度须为 6 到 256 个字符"))
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", code.encode(), bytes.fromhex(salt), 310000).hex()
        return {"enabled": True, "salt": salt, "hash": digest}

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".access-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @property
    def enabled(self):
        return self.state.get("enabled") is True

    def verify(self, code):
        if not isinstance(code, str) or len(code) > 256 or not self.enabled:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", code.encode(), bytes.fromhex(self.state["salt"]), 310000).hex()
        return hmac.compare_digest(digest, self.state["hash"])

    def session(self, headers):
        try:
            cookie = SimpleCookie(headers.get("Cookie", ""))
            sid = cookie[COOKIE].value if COOKIE in cookie else ""
        except Exception:
            return ""
        with self.lock:
            if self.sessions.get(sid, 0) > time.time():
                return sid
            self.sessions.pop(sid, None)
        return ""

    def admit(self, headers):
        with self.lock:
            sid = self.session(headers)
            if sid:
                return sid, False
            if self.enabled:
                return "", False
            return self.issue(), True

    def issue(self):
        with self.lock:
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            if len(self.sessions) >= 256:
                self.sessions.pop(next(iter(self.sessions)))
            sid = secrets.token_urlsafe(32)
            self.sessions[sid] = now + SESSION_AGE
            return sid

    def valid(self, sid):
        # A lookup is atomic; do not invert the access/terminal lock order.
        return self.sessions.get(sid, 0) > time.time()

    def check_code(self, code, ip):
        """Shared browser/API rate limit; no session is created for Bearer calls."""
        with self.lock:
            if not self.enabled:
                return 200
            now = time.monotonic()
            self.attempts = {k: v for k, v in self.attempts.items() if now - v[0] < 60}
            start, count = self.attempts.get(ip, (now, 0))
            if count >= 8 or sum(v[1] for v in self.attempts.values()) >= 80:
                return 429
            # The same machine credential is sent on every API/MCP request.
            # Cache only successful checks, under a process-keyed digest, so the
            # expensive KDF does not serialize normal browser/API traffic.
            key = hmac.digest(self._verification_key, code.encode(), "sha256") if isinstance(code, str) and len(code) <= 256 else b""
            if self._verified.get(key, 0) <= now:
                if not self.verify(code):
                    self.attempts[ip] = (start, count + 1)
                    return 401
                self._verified = {key: now + 30}
            self.attempts.pop(ip, None)
            return 200

    def login(self, code, ip):
        with self.lock:
            status = self.check_code(code, ip)
            return (self.issue() if status == 200 else ""), status

    def update(self, body):
        with self.lock:
            if type(body.get("enabled")) is not bool:
                raise ValueError(tr("授权码开关必须是布尔值"))
            if self.enabled and not self.verify(body.get("current_code")):
                raise ValueError(tr("当前访问授权码不正确"))
            candidate = self._record(body.get("code", "")) if body["enabled"] else {"enabled": False}
            self._save(candidate)
            self.state = candidate
            self._verified.clear()
            self.sessions.clear()
            self.changed()
            return self.issue()

    def logout(self, sid):
        with self.lock:
            self.sessions.pop(sid, None)

    @staticmethod
    def cookie(sid, secure=False):
        return f"{COOKIE}={sid}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_AGE if sid else 0}" + ("; Secure" if secure else "")

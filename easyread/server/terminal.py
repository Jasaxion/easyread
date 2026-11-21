"""Managed loopback ttyd sessions, proxied through EasyRead's access-code gate."""
from __future__ import annotations

import http.client
import os
import secrets
import select
import shutil
import signal
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

from ..app import config, proxy
from ..app.i18n import tr

ACTIONS = {"shell", "claude-login", "codex-login", "claude-status", "codex-status", "claude-update", "codex-update",
           "pi-login", "pi-status", "pi-install", "pi-update"}


class Terminals:
    def __init__(self, access):
        self.access = access
        self.lock = threading.RLock()
        self.sessions = {}
        self.enabled = os.environ.get("EASYREAD_TERMINAL") == "1" and bool(shutil.which("ttyd"))

    def open(self, sid, action):
        if not self.enabled:
            raise ValueError(tr("当前部署未启用内嵌终端"))
        if action not in ACTIONS:
            raise ValueError("Unknown CLI action")
        with self.lock:
            self.close(sid)
            self._reap()
            if len(self.sessions) >= 4:
                raise ValueError(tr("终端会话已满，请先关闭其他终端"))
            if not self.access.valid(sid):
                raise ValueError(tr("访问会话已失效，请重新进入"))
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            key = secrets.token_hex(16)
            prefix = "/terminal/" + key
            command = [shutil.which("ttyd"), "--interface", "127.0.0.1", "--port", str(port),
                       "--writable", "--check-origin", "--max-clients", "1", "--base-path", prefix,
                       "--cwd", str(config.HOME), "/usr/local/bin/easyread-terminal-session", action]
            proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True, env=dict(os.environ))
            entry = {"sid": sid, "proc": proc, "port": port, "created": time.monotonic(), "sockets": set(), "action": action}
            self.sessions[key] = entry
            for _ in range(50):
                if proc.poll() is not None:
                    break
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.1):
                        return {"url": prefix + "/", "action": action}
                except OSError:
                    time.sleep(.04)
            self._stop(key)
            raise ValueError(tr("终端启动失败，请查看运行日志"))

    def _reap(self):
        for key, entry in list(self.sessions.items()):
            if not self.access.valid(entry["sid"]) or entry["proc"].poll() is not None or time.monotonic() - entry["created"] > 12 * 3600:
                self._stop(key)

    def _stop(self, key):
        entry = self.sessions.pop(key, None)
        if not entry:
            return
        for sock in list(entry["sockets"]):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            os.killpg(entry["proc"].pid, signal.SIGTERM)
            entry["proc"].wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(entry["proc"].pid, signal.SIGKILL)
            entry["proc"].wait(timeout=2)
        except ProcessLookupError:
            pass

    def close(self, sid=None, url=None):
        target = urlsplit(url).path.split("/")[2] if isinstance(url, str) and url.startswith("/terminal/") else None
        with self.lock:
            for key, entry in list(self.sessions.items()):
                if (sid is None or entry["sid"] == sid) and (target is None or key == target):
                    self._stop(key)

    def relay(self, handler, sid):
        path = urlsplit(handler.path).path
        parts = path.split("/")
        key = parts[2] if len(parts) > 2 else ""
        with self.lock:
            self._reap()
            entry = self.sessions.get(key)
            if not entry or entry["sid"] != sid:
                handler._json(403, {"error": tr("终端会话已关闭，请重新打开")})
                return
            port = entry["port"]
        upgrade = handler.headers.get("Upgrade", "").lower() == "websocket"
        host = handler.headers.get("Host", "")
        if upgrade and handler.headers.get("Origin") not in (f"http://{host}", f"https://{host}"):
            handler._json(403, {"error": "bad origin"})
            return
        if not upgrade:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("GET", handler.path, headers={"Host": host})
                response = conn.getresponse()
                body = response.read()
                handler.send_response(response.status)
                handler.send_header("Content-Type", response.getheader("Content-Type", "application/octet-stream"))
                handler.send_header("Content-Length", str(len(body)))
                handler.send_header("Cache-Control", "no-store")
                handler.send_header("X-Frame-Options", "SAMEORIGIN")
                handler.send_header("Content-Security-Policy", "frame-ancestors 'self'")
                handler.end_headers()
                if handler.command != "HEAD":
                    handler.wfile.write(body)
            finally:
                conn.close()
            return
        upstream = socket.create_connection(("127.0.0.1", port), timeout=5)
        client = handler.connection
        with self.lock:
            if self.sessions.get(key) is not entry or not self.access.valid(sid):
                upstream.close()
                handler._json(403, {"error": "session expired"})
                return
            entry["sockets"].update((upstream, client))
        try:
            names = ("Host", "Upgrade", "Connection", "Origin", "Sec-WebSocket-Key", "Sec-WebSocket-Version", "Sec-WebSocket-Protocol")
            request = f"GET {handler.path} HTTP/1.1\r\n" + "".join(f"{name}: {handler.headers[name]}\r\n" for name in names if name in handler.headers) + "\r\n"
            upstream.sendall(request.encode("latin-1"))
            handler.close_connection = True
            # Forward the handshake and frames unchanged; ttyd owns the PTY protocol.
            while self.access.valid(sid):
                readable, _, _ = select.select([client, upstream], [], [], 1)
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is client else client).sendall(data)
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                entry["sockets"].discard(client)
                entry["sockets"].discard(upstream)
                if self.sessions.get(key) is entry:
                    self._stop(key)
            upstream.close()


def status():
    items = []
    for name in ("claude", "codex", "pi"):
        exe = shutil.which(name)
        version = ""
        if exe:
            try:
                result = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
                version = result.stdout.strip().splitlines()[0][:100]
            except (OSError, IndexError, subprocess.TimeoutExpired):
                pass
        items.append({"id": name, "installed": bool(exe), "version": version})
    return {"items": items}

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

    def available(self):
        return self.enabled

    def _reap(self):
        for key, entry in list(self.sessions.items()):
            if not self.available() or not self.access.valid(entry["sid"]) or entry["proc"].poll() is not None or time.monotonic() - entry["created"] > 12 * 3600:
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
        try:
            headers = relay_headers(handler)
        except ValueError as exc:
            handler._json(403, {"error": str(exc)})
            return
        path = urlsplit(handler.path).path
        parts = path.split("/")
        key = parts[2] if len(parts) > 2 else ""
        with self.lock:
            self._reap()
            entry = self.sessions.get(key)
            if not entry or entry["sid"] != sid or not self.access.valid(sid):
                handler._json(403, {"error": tr("终端会话已关闭，请重新打开")})
                return
            port = entry["port"]
        upgrade = headers.get("Upgrade", "").lower() == "websocket"
        host = headers['Host']
        if not upgrade:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request(handler.command, handler.path, headers={"Host": host})
                response = conn.getresponse()
                relay_response(handler, response)
            finally:
                conn.close()
            return
        upstream = socket.create_connection(("127.0.0.1", port), timeout=5)
        client = handler.connection
        with self.lock:
            if self.sessions.get(key) is not entry or not self.access.valid(sid) or entry.get('attached'):
                upstream.close()
                handler._json(403, {"error": "session expired"})
                return
            entry['attached'] = True
            entry["sockets"].update((upstream, client))
        try:
            request = f"GET {handler.path} HTTP/1.1\r\n" + "".join(f"{name}: {value}\r\n" for name, value in headers.items()) + "\r\n"
            upstream.sendall(request.encode("latin-1"))
            handler.close_connection = True
            # Forward the handshake and frames unchanged; ttyd owns the PTY protocol.
            relay_stream(client, upstream, lambda: self.available() and self.access.valid(sid))
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
        version, error = "", ""
        if exe:
            try:
                result = subprocess.run([exe, "--version"], stdin=subprocess.DEVNULL, capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", timeout=5)
                if result.returncode:
                    error = "command_failed"
                else:
                    output = (result.stdout or result.stderr or "").strip().splitlines()
                    version = output[0][:100] if output else ""
            except subprocess.TimeoutExpired:
                error = "timeout"
            except OSError:
                error = "launch_failed"
        item = {"id": name, "installed": bool(exe), "version": version}
        # Installation and a successful version probe are different facts. Do
        # not hide an installed CLI or return raw error output/credentials.
        if error:
            item["version_error"] = error
        items.append(item)
    return {"items": items}


def relay_headers(handler):
    """Do not forward browser credentials, forwarding hints or ambiguous headers."""
    from . import network
    headers = handler.headers
    host = headers.get('Host', '')
    allowed = getattr(handler, 'allowed_hosts', frozenset({network.host_name(host)}))
    if not network.check_host(headers, allowed):
        raise ValueError('bad host')
    names = ('Host', 'Upgrade', 'Connection', 'Origin', 'Sec-WebSocket-Key',
             'Sec-WebSocket-Version', 'Sec-WebSocket-Protocol')
    result = {}
    for name in names:
        values = headers.get_all(name, [])
        if len(values) > 1 or any('\r' in v or '\n' in v for v in values):
            raise ValueError('ambiguous terminal headers')
        if values:
            result[name] = values[0]
    if result.get('Upgrade', '').lower() == 'websocket':
        if (handler.command != 'GET' or result.get('Origin') not in (f'http://{host}', f'https://{host}')
                or headers.get('Sec-Fetch-Site') == 'cross-site'):
            raise ValueError('bad origin')
    return result


def relay_response(handler, response):
    """Bounded HTTP asset forwarding; never expose worker headers or redirects."""
    handler.send_response(response.status)
    handler.send_header('Content-Type', response.getheader('Content-Type', 'application/octet-stream'))
    length = response.getheader('Content-Length')
    if length is not None:
        handler.send_header('Content-Length', length)
    else:
        handler.close_connection = True
        handler.send_header('Connection', 'close')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Frame-Options', 'SAMEORIGIN')
    handler.send_header('Content-Security-Policy', "frame-ancestors 'self'")
    handler.end_headers()
    if handler.command != 'HEAD':
        try:
            while True:
                body = response.read(65536)
                if not body:
                    break
                handler.wfile.write(body)
        except (OSError, http.client.HTTPException):
            response.will_close = True
            handler.close_connection = True
    else:
        response.read()


def relay_stream(client, upstream, alive):
    """Backpressure in both directions, with no terminal output/history buffer."""
    sockets = (client, upstream)
    timeouts = [sock.gettimeout() for sock in sockets]
    pending = {sock: bytearray() for sock in sockets}
    try:
        for sock in sockets:
            sock.setblocking(False)
        while alive():
            readable, writable, _ = select.select(
                [sock for sock, other in ((client, upstream), (upstream, client)) if not pending[other]],
                [sock for sock in sockets if pending[sock]], [], 1)
            for source in readable:
                try:
                    chunk = source.recv(65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    return
                pending[upstream if source is client else client].extend(chunk)
            for target in writable:
                try:
                    count = target.send(pending[target])
                except BlockingIOError:
                    continue
                if not count:
                    return
                del pending[target][:count]
    except (OSError, ValueError):
        pass
    finally:
        for sock, timeout in zip(sockets, timeouts):
            try:
                sock.settimeout(timeout)
            except OSError:
                pass

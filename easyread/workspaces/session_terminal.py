"""Native CLI TUI terminals for research-workspace sessions.

Reuses the existing ttyd relay (``easyread/server/terminal.py``) unchanged: only
``open`` is replaced so the terminal runs the project's native CLI in the project
workspace with the project's CLI home, under its own URL prefix. The access-code
gate, origin checks, reaping and websocket relay all come from the base class.
"""
from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path

from ..app.i18n import tr
from ..server.terminal import Terminals
from . import session_cli as cli

PREFIX = "/workspace-terminal"
MAX_SESSIONS = 4


class NativeTerminals(Terminals):
    """ttyd sessions bound to (project, session) scopes."""

    def scope_active(self, pid=None, sid=None) -> bool:
        with self.lock:
            self._reap()
            return any((pid is None or entry.get("scope", (None, None))[0] == pid)
                       and (sid is None or entry.get("scope", (None, None))[1] == sid)
                       for entry in self.sessions.values())

    def scope_url(self, pid, sid, access_sid):
        with self.lock:
            self._reap()
            for key, entry in self.sessions.items():
                if entry.get("scope") == (pid, sid):
                    if entry["sid"] != access_sid or not self.access.valid(access_sid):
                        raise ValueError(tr("访问会话已失效，请重新进入"))
                    return f"{PREFIX}/{key}/"
        return None

    def _stop(self, key):
        entry = self.sessions.get(key)
        if not entry:
            return
        for sock in list(entry["sockets"]):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        cli.stop_process(entry["proc"])
        # Keep the reservation if shutdown fails; never allow concurrent writers.
        self.sessions.pop(key, None)

    def open_native(self, *, access_sid, scope, argv, cwd, env):
        if not enabled():  # Checked live: deployments may toggle the switch after start.
            raise ValueError(unavailable_reason() or tr("当前部署未启用内嵌终端"))
        with self.lock:
            self._reap()
            if any(entry.get("scope") == tuple(scope) for entry in self.sessions.values()):
                raise ValueError("Native terminal is already open")
            if len(self.sessions) >= MAX_SESSIONS:
                raise ValueError(tr("终端会话已满，请先关闭其他终端"))
            if not self.access.valid(access_sid):
                raise ValueError(tr("访问会话已失效，请重新进入"))
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            key = secrets.token_hex(16)
            prefix = f"{PREFIX}/{key}"
            command = [shutil.which("ttyd"), "--interface", "127.0.0.1", "--port", str(port),
                       "--writable", "--check-origin", "--max-clients", "1", "--base-path", prefix,
                       "--cwd", str(cwd), *argv]
            proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=os.name != "nt", cwd=str(cwd), env=dict(env),
                                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0)
            self.sessions[key] = {"sid": access_sid, "proc": proc, "port": port, "created": time.monotonic(),
                                  "sockets": set(), "action": "workspace", "scope": tuple(scope)}
            for _ in range(50):
                if proc.poll() is not None:
                    break
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.1):
                        return {"url": prefix + "/", "scope": list(scope)}
                except OSError:
                    time.sleep(.04)
            self._stop(key)
            raise ValueError(tr("终端启动失败，请查看运行日志"))

    def close_scope(self, pid=None, sid=None) -> None:
        with self.lock:
            for key, entry in list(self.sessions.items()):
                scope = entry.get("scope") or (None, None)
                if (pid is None or scope[0] == pid) and (sid is None or scope[1] == sid):
                    self._stop(key)


def ttyd_available() -> bool:
    return bool(shutil.which("ttyd"))


def enabled() -> bool:
    return os.environ.get("EASYREAD_TERMINAL") == "1" and ttyd_available()


def unavailable_reason() -> str:
    if not ttyd_available():
        return tr("未安装 ttyd，无法打开原生终端界面")
    if os.environ.get("EASYREAD_TERMINAL") != "1":
        return tr("当前部署未启用内嵌终端")
    return ""


def workspace_cwd(root) -> Path:
    path = Path(root)
    if not path.is_dir():
        raise ValueError(tr("项目目录不存在"))
    return path

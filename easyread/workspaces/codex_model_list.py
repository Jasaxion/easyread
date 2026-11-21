"""Bounded native metadata query. Never starts a model thread/turn or CLI session."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import tempfile
import threading
import time

from . import session_cli as cli

TIMEOUT = 8.0
MAX_MODELS = 200
MAX_OUTPUT = 8 * 1024 * 1024
MAX_LINE = 1024 * 1024


def query(executable: str, home: Path) -> dict:
    """Only initialize/model-list RPCs, in an empty cwd outside the project.

    stderr and protocol errors are deliberately not returned: native diagnostics
    can contain credentials, provider URLs and configuration paths.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="easyread-model-list-") as cwd:
            env = dict(os.environ)
            env["CODEX_HOME"] = str(home.resolve())
            env.pop("EASYREAD_TERMINAL", None)
            process = subprocess.Popen([executable, "--cd", cwd, "app-server"], cwd=cwd, env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, start_new_session=os.name != "nt",
                                       creationflags=cli._NO_WINDOW)
            return _exchange(process)
    except TimeoutError:
        return {"models": [], "default": "", "warning": "model_list_timeout"}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return {"models": [], "default": "", "warning": "model_list_unavailable"}


def _exchange(process) -> dict:
    messages = queue.Queue(maxsize=32)
    overflow = threading.Event()

    def read():
        total = 0
        try:
            while True:
                line = process.stdout.readline(MAX_LINE + 1)
                total += len(line)
                if len(line) > MAX_LINE or total > MAX_OUTPUT:
                    overflow.set()
                    break
                messages.put_nowait(line)
                if not line:
                    break
        except (OSError, ValueError, queue.Full):
            overflow.set()
        finally:
            try:
                messages.put_nowait(b"")
            except queue.Full:
                pass

    reader = threading.Thread(target=read, name="easyread-model-list", daemon=True)
    deadline = time.monotonic() + TIMEOUT

    def send(message):
        process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        process.stdin.flush()

    def response(request_id):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if overflow.is_set():
                raise ValueError("Output limit")
            try:
                line = messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError() from None
            if not line:
                raise ValueError("End of protocol")
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("Invalid protocol")
            if message.get("id") == request_id:
                if "error" in message or not isinstance(message.get("result"), dict):
                    raise ValueError("Unsupported protocol")
                return message["result"]

    try:
        reader.start()
        send({"id": 0, "method": "initialize", "params": {
            "clientInfo": {"name": "easyread-model-catalog", "version": "1"}}})
        response(0)
        send({"method": "initialized"})
        models, default, cursor, cursors, count = [], "", None, set(), 0
        # Both rows and page count are bounded, even if a CLI ignores limit.
        for request_id in range(1, 5):
            send({"id": request_id, "method": "model/list", "params": {
                "limit": MAX_MODELS - count, "cursor": cursor}})
            page = response(request_id)
            data = page.get("data")
            if not isinstance(data, list):
                raise ValueError("Invalid model list")
            for row in data[:MAX_MODELS - count]:
                count += 1
                if not isinstance(row, dict) or row.get("hidden") is True:
                    continue
                model = row.get("model") or row.get("id")
                models.append({"id": model, "name": row.get("displayName"), "desc": row.get("description")})
                if row.get("isDefault") is True:
                    default = model
            cursor = page.get("nextCursor")
            if not cursor or count >= MAX_MODELS:
                break
            if not isinstance(cursor, str) or len(cursor) > 1024 or cursor in cursors:
                raise ValueError("Invalid model cursor")
            cursors.add(cursor)
        return {"models": models, "default": default, **({} if models else {"warning": "model_list_unavailable"})}
    finally:
        # Kill before joining: a stalled CLI may never close stdout on its own.
        _stop(process)
        if reader.ident is not None:
            reader.join(timeout=1)


def _stop(process):
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        elif process.poll() is None:
            process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass
        process.wait(timeout=1)
    if os.name != "nt":
        # Reap pipe-holding descendants even if the immediate process exited
        # on SIGTERM; otherwise closing stdout could wait on its reader thread.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    for stream in (process.stdin, process.stdout):
        try:
            stream.close()
        except OSError:
            pass

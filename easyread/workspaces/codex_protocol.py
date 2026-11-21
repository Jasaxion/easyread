"""Codex App Server JSON-RPC over private stdio (installed schema, not exec JSONL).

Reader and notification consumer are separate: native replies can arrive while
an event is being persisted, and a notification handler never waits on an RPC.
No transport is exposed outside the authenticated workspace HTTP adapter.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import tempfile
import threading
import time

from . import session_cli as cli

MAX_LINE = 8 * 1024 * 1024
REQUEST_METHODS = {
    "item/tool/requestUserInput", "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval", "item/permissions/requestApproval", "mcpServer/elicitation/request",
}
_PROBES = {}
_PROBE_LOCK = threading.Lock()


def request_key(value):
    # JSON-RPC integer 7 and string "7" are different; bool is not an ID.
    if type(value) not in (str, int):
        raise cli.SessionError("invalid_parameter", "Native request ID must be a string or integer")
    return type(value).__name__, value


def probe(exe=None):
    """Safe cached CLI/schema reads in a neutral cwd, never a project runtime."""
    exe = exe or cli.executable("codex")
    empty = {"goal": False, "steer": False, "user_input": False, "app_server": False,
             "approvals": False, "elicitation": False, "background_terminals": False}
    if not exe:
        return {**empty, "reason": "Codex CLI is not installed", "methods": []}
    try:
        key = (exe, Path(exe).stat().st_mtime_ns)
    except OSError:
        return {**empty, "reason": "Codex CLI is unavailable", "methods": []}
    with _PROBE_LOCK:
        hit = _PROBES.get(key)
        if hit and time.monotonic() - hit[0] < 300:
            return dict(hit[1])
        result = {**empty, "methods": []}
        try:
            with tempfile.TemporaryDirectory(prefix="easyread-codex-probe-") as tmp:
                schema = Path(tmp) / "schema"
                done = subprocess.run([exe, "app-server", "generate-json-schema", "--experimental", "--out", str(schema)],
                                      cwd=tmp, capture_output=True, timeout=20, creationflags=cli._NO_WINDOW)
                if done.returncode:
                    raise ValueError("Installed Codex does not expose the required App Server schema")
                client = json.loads((schema / "ClientRequest.json").read_text())
                server = json.loads((schema / "ServerRequest.json").read_text())
                def methods(doc):
                    return {method for entry in doc.get("oneOf", [])
                            for method in entry.get("properties", {}).get("method", {}).get("enum", [])}
                supported, requests = methods(client), methods(server)
                features = subprocess.run([exe, "features", "list"], cwd=tmp, capture_output=True,
                                          text=True, timeout=10, creationflags=cli._NO_WINDOW)
                enabled = {words[0] for line in features.stdout.splitlines()
                           if len(words := line.split()) >= 3 and words[-1] == "true"}
                result.update(app_server={"initialize", "thread/start", "thread/resume", "turn/start"} <= supported,
                              goal={"thread/goal/set", "thread/goal/get", "thread/goal/clear"} <= supported and "goals" in enabled,
                              steer="turn/steer" in supported,
                              user_input="item/tool/requestUserInput" in requests and "default_mode_request_user_input" in enabled,
                              approvals="item/commandExecution/requestApproval" in requests,
                              elicitation="mcpServer/elicitation/request" in requests,
                              background_terminals="thread/backgroundTerminals/list" in supported,
                              methods=sorted(supported), reason="")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result["reason"] = str(error)
        _PROBES[key] = (time.monotonic(), result)
        return dict(result)


class RpcError(cli.SessionError):
    def __init__(self, error):
        self.native_code = error.get("code")
        super().__init__("native_rpc_error", str(error.get("message") or "Native RPC failed"), 409)


class AppServer:
    def __init__(self, argv, cwd, env, on_message, on_exit):
        self.generation = secrets.token_hex(16)
        self.on_message, self.on_exit = on_message, on_exit
        self._lock = threading.Lock()
        self._waiters = {}
        self._next = 0
        self._closed = False
        self._intentional = False
        self._events = queue.Queue(maxsize=256)
        self._writes = queue.Queue(maxsize=64)
        self.proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, start_new_session=os.name != "nt",
                                     creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0)
        self.reader = threading.Thread(target=self._read, name="codex-rpc-reader", daemon=True)
        self.consumer = threading.Thread(target=self._consume, name="codex-rpc-events", daemon=True)
        self.stderr = threading.Thread(target=self._drain, name="codex-rpc-stderr", daemon=True)
        self.writer = threading.Thread(target=self._send, name="codex-rpc-writer", daemon=True)
        self.writer.start()
        self.consumer.start()
        self.reader.start()
        self.stderr.start()

    def initialize(self):
        result = self.call("initialize", {"clientInfo": {"name": "easyread", "version": "1", "title": "EasyRead"},
                                          "capabilities": {"experimentalApi": True}})
        self.notify("initialized", {})
        return result

    def _write(self, message):
        raw = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        if len(raw) > MAX_LINE:
            raise cli.SessionError("invalid_parameter", "Native request exceeds the protocol frame limit")
        if self._closed:
            raise cli.SessionError("runtime_expired", "Native runtime is no longer connected", 409)
        sent, box = threading.Event(), {}
        try:
            self._writes.put((raw, sent, box), timeout=5)
        except queue.Full:
            raise cli.SessionError("rpc_uncertain", "Native write queue is stalled; reconcile before retrying", 409) from None
        if not sent.wait(10):
            raise cli.SessionError("rpc_uncertain", "Native input is stalled; request outcome is uncertain", 409)
        if box.get("error"):
            raise cli.SessionError("runtime_expired", "Native runtime disconnected", 409)

    def _send(self):
        while True:
            try:
                raw, sent, box = self._writes.get(timeout=.2)
            except queue.Empty:
                if self._closed:
                    return
                continue
            try:
                if self._closed:
                    raise OSError("closed")
                self.proc.stdin.write(raw)
                self.proc.stdin.flush()
            except (OSError, ValueError):
                box["error"] = True
            finally:
                sent.set()
                self._writes.task_done()

    def call(self, method, params, timeout=30):
        event, box = threading.Event(), {}
        with self._lock:
            self._next += 1
            rid = "easyread-" + str(self._next)
            self._waiters[request_key(rid)] = (event, box)
        try:
            self._write({"id": rid, "method": method, "params": params})
            if not event.wait(timeout):
                # Never retry a mutation: the native side might have accepted it.
                raise cli.SessionError("rpc_uncertain", "Native request timed out; reconcile this session before retrying", 409)
            if "error" in box:
                raise RpcError(box["error"])
            return box.get("result") or {}
        finally:
            with self._lock:
                self._waiters.pop(request_key(rid), None)

    def notify(self, method, params):
        self._write({"method": method, "params": params})

    def reply(self, rid, result=None, error=None):
        request_key(rid)
        self._write({"id": rid, **({"error": error} if error else {"result": result})})

    def _read(self):
        reason = "Native App Server disconnected; running work needs reconciliation"
        try:
            while True:
                line = self.proc.stdout.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    raise ValueError("Native protocol frame exceeded the size limit")
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Invalid native protocol frame")
                if "method" not in message and "id" in message:
                    with self._lock:
                        waiter = self._waiters.get(request_key(message["id"]))
                        if waiter:
                            waiter[1].update(message)
                            waiter[0].set()
                else:
                    self._events.put(message)
        except (OSError, ValueError) as error:
            reason = str(error)
        finally:
            self._closed = True
            with self._lock:
                for event, box in self._waiters.values():
                    box["error"] = {"code": -32000, "message": reason}
                    event.set()
            self._events.put({"_exit": reason})

    def _consume(self):
        failed = False
        while True:
            message = self._events.get()
            try:
                if "_exit" in message:
                    if not failed:
                        self.on_exit(message["_exit"], self._intentional)
                    return
                if not failed:
                    self.on_message(message)
            except Exception as error:
                # Keep draining to unblock the reader even if persistence failed.
                failed = True
                self._intentional = False
                try:
                    self.on_exit("Native event could not be saved: " + str(error), False)
                except Exception:
                    pass  # Runtime retains its failed-persistence reservation.
                try:
                    cli.stop_process(self.proc)
                except Exception:
                    pass
                if "_exit" in message:
                    return
            finally:
                self._events.task_done()

    def _drain(self):
        # Do not expose native stderr (may contain credentials/config) as UI logs.
        try:
            while self.proc.stderr.read(8192):
                pass
        except (OSError, ValueError):
            pass

    def close(self):
        self._intentional = True
        cli.stop_process(self.proc)
        if threading.current_thread() is not self.reader:
            self.reader.join(5)
        if threading.current_thread() is not self.consumer:
            self.consumer.join(5)
        self.stderr.join(2)
        self.writer.join(2)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            stream.close()
        if self.reader.is_alive() or self.consumer.is_alive() or self.writer.is_alive() or self.proc.poll() is None:
            raise cli.SessionError("conflict", "Native runtime is still stopping", 409)

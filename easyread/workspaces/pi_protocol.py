"""Pi's typed JSONL protocol on the existing bounded stdio transport.

All Pi records, including acknowledgements, cross the same consumer barrier.
A prompt acknowledgement may wait for extension UI: it is never an HTTP wait.
"""
from __future__ import annotations

import json
import secrets
import threading

from . import session_cli as cli
from ..chat.images import MAX_REQUEST
from .codex_protocol import AppServer, request_key


class UnwrittenError(cli.SessionError):
    """Payload validation failed before admission to the transport write queue."""


class UnstartedError(cli.SessionError):
    """Popen failed without creating a surviving native writer."""


class PiRPC(AppServer):
    # Match the existing 20 MiB decoded-image budget plus base64/text overhead.
    max_line = MAX_REQUEST

    def __init__(self, argv, cwd, env, on_message, on_exit, *, generation=None):
        self.receive = on_message
        self.startup_error = ""
        super().__init__(argv, cwd, env, self._dispatch, on_exit, generation=generation)

    def _spawn(self, argv, cwd, env):
        try:
            return super()._spawn(argv, cwd, env)
        except OSError as error:
            raise UnstartedError("engine_unavailable", "Pi could not be started: " + str(error), 409) from error

    def _encode(self, message):
        try:
            return super()._encode(message)
        except cli.SessionError as error:
            raise UnwrittenError("payload_too_large", "Pi input exceeds the native frame limit; it was not submitted", 413) from error

    def reject_startup(self, reason):
        with self._lock:
            self.startup_error = reason
            for event, box in self._waiters.values():
                box["startup_error"] = reason
                event.set()

    def submit(self, rid, command, **params):
        self._write({"id": rid, "type": command, **params})

    def call(self, command, params=None, timeout=8):
        rid = "query-" + secrets.token_hex(12)
        event, box = threading.Event(), {}
        with self._lock:
            if self.startup_error:
                raise cli.SessionError("startup_ui_unsupported", self.startup_error, 409)
            self._waiters[request_key(rid)] = (event, box)
        try:
            self.submit(rid, command, **(params or {}))
            if not event.wait(timeout):
                raise cli.SessionError("rpc_uncertain", "Pi did not acknowledge the operation; do not retry automatically", 409)
            if box.get("startup_error"):
                raise cli.SessionError("startup_ui_unsupported", box["startup_error"], 409)
            if box.get("transport_error"):
                raise cli.SessionError("runtime_expired", box["transport_error"], 409)
            if box.get("success") is not True:
                raise cli.SessionError("native_rpc_error", str(box.get("error") or "Pi rejected the operation")[:2000], 409)
            data = box.get("data") or {}
            if command == "get_state" and isinstance(data, dict):
                data["_rpc_cursor"] = box.get("_notification_cursor", 0)
            return data
        finally:
            with self._lock:
                self._waiters.pop(request_key(rid), None)

    def ui_reply(self, rid, **answer):
        self._write({"type": "extension_ui_response", "id": rid, **answer})

    def _dispatch(self, message):
        if message["type"] == "response":
            if not isinstance(message.get("id"), str) or type(message.get("success")) is not bool:
                raise ValueError("Uncorrelated or malformed Pi response")
            with self._lock:
                waiter = self._waiters.get(request_key(message["id"]))
                if waiter:
                    waiter[1].update(message)
                    waiter[0].set()
                    return
        self.receive(message)

    def _read(self):
        reason = "Pi RPC disconnected; reconcile before explicitly resuming"
        try:
            while True:
                line = self.proc.stdout.readline(self.max_line + 1)
                if not line:
                    break
                if len(line) > self.max_line or not line.endswith(b"\n"):
                    raise ValueError("Invalid Pi JSONL frame size or termination")
                message = json.loads(line)
                if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                    raise ValueError("Invalid Pi JSONL record")
                self._notification_cursor += 1
                self._events.put((line, self._notification_cursor), size=len(line))
        except (OSError, ValueError) as error:
            reason = str(error)
        finally:
            self._closed = True
            with self._lock:
                for event, box in self._waiters.values():
                    box["transport_error"] = reason
                    event.set()
            self._events.put((None, reason))

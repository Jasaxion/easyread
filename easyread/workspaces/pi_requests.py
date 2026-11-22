"""Generation-scoped Pi extension dialogs; no implicit approval or editor edits."""
from __future__ import annotations

import json
import time

from . import session_cli as cli

DIALOGS = {"select", "confirm", "input", "editor"}
NOTICES = {"notify", "setStatus", "setWidget", "setTitle", "set_editor_text"}
MAX_PENDING_BYTES = 1024 * 1024
STARTUP_UI_UNSUPPORTED = (
    "Pi requested a blocking startup dialog before RPC input was initialized. Pi 1.6 waits for session_start "
    "hooks before attaching its stdin reader, so this dialog cannot consume a GUI response. Stop this runtime "
    "and defer the extension dialog until a command runs, use the native terminal, or upgrade to a Pi/adapter "
    "combination verified to initialize RPC input before startup hooks. No answer was sent.")


def receive(runtime, event):
    rid, method = event.get("id"), event.get("method")
    if not isinstance(rid, str) or not rid or len(rid) > 256:
        raise ValueError("Invalid Pi extension request identity")
    if method in DIALOGS:
        card = {"id": rid, "generation": runtime.generation, "kind": "pi_ui", "method": method,
                "is_blocking": True, "title": str(event.get("title") or "Pi extension")[:2000]}
        for key in ("message", "placeholder", "prefill"):
            if key in event:
                if not isinstance(event[key], str) or len(event[key]) > 200000:
                    raise ValueError("Invalid Pi dialog text")
                card[key] = event[key]
        if method == "select":
            options = event.get("options")
            if not isinstance(options, list) or len(options) > 256 or any(not isinstance(v, str) or len(v) > 4000 for v in options):
                raise ValueError("Invalid Pi select options")
            card["options"] = options
        timeout = event.get("timeout")
        if timeout is not None and (type(timeout) not in (int, float) or not 0 <= timeout <= 86400000):
            raise ValueError("Invalid Pi dialog timeout")
        deadline = time.monotonic() + timeout / 1000 if timeout is not None else None
        if timeout is not None:
            card["expires_at"] = time.time() + timeout / 1000
        size = len(json.dumps(card).encode())
        if rid in runtime.pending or len(runtime.pending) >= 32 or size + sum(v[2] for v in runtime.pending.values()) > MAX_PENDING_BYTES:
            raise ValueError("Pi extension dialog limit exceeded")
        if runtime.starting:
            card.update(expired=True, unsupported=True, is_blocking=False,
                        error_code="startup_ui_unsupported", error=STARTUP_UI_UNSUPPORTED)
            runtime.startup_ui_error = STARTUP_UI_UNSUPPORTED
            runtime.error = STARTUP_UI_UNSUPPORTED
            runtime.uncertain = True
        runtime.pending[rid] = (card, deadline, size)
        if runtime.starting:
            # Publish the diagnostic card before waking the failed startup call.
            runtime.persist()
            runtime.rpc.reject_startup(STARTUP_UI_UNSUPPORTED)
            runtime.notice(STARTUP_UI_UNSUPPORTED)
    else:
        # These are notices, not commands to mutate the browser's unsent draft.
        text = event.get("message") or event.get("statusText") or event.get("title") or ""
        if method == "setWidget":
            text = "\n".join(str(line) for line in (event.get("widgetLines") or [])[:30])
        if method == "set_editor_text":
            text = "Pi suggested editor text (your draft was not changed):\n" + str(event.get("text") or "")
        if method not in NOTICES:
            text = "Unsupported Pi extension UI: " + str(method) + ". Use the exclusive native terminal for TUI-only interfaces."
        runtime.notice(str(text)[:4000])


def expire(runtime):
    now = time.monotonic()
    expired = [rid for rid, (_, deadline, _) in runtime.pending.items() if deadline is not None and now >= deadline]
    deadlines = [runtime.pending[rid][1] for rid in expired]
    for rid in expired:
        runtime.pending.pop(rid, None)
    if expired:
        # Resume from the last actual timeout, not the next maintenance tick.
        runtime.update_ack_pause(now=max(deadlines))
    return bool(expired)


def answer(runtime, body):
    rid = body.get("request_id")
    if not isinstance(rid, str):
        raise cli.SessionError("invalid_parameter", "Pi request ID must be a string")
    with runtime.events_lock:
        if runtime.startup_ui_error and body.get("generation") == runtime.generation:
            raise cli.SessionError("startup_ui_unsupported", runtime.startup_ui_error, 409)
        if expire(runtime):
            runtime.persist()
        pending = runtime.pending.get(rid)
        if runtime.closed or body.get("generation") != runtime.generation or not pending:
            raise cli.SessionError("request_expired", "This Pi dialog has expired or belongs to another runtime", 409)
        card = pending[0]
        if body.get("cancelled") is True:
            response = {"cancelled": True}
        elif card["method"] == "confirm":
            if type(body.get("confirmed")) is not bool:
                raise cli.SessionError("invalid_parameter", "Pi confirmation requires confirmed: true or false")
            response = {"confirmed": body["confirmed"]}
        else:
            value = body.get("value")
            if not isinstance(value, str) or len(value) > 200000:
                raise cli.SessionError("invalid_parameter", "Pi dialog response must be text of at most 200000 characters")
            if card["method"] == "select" and value not in card["options"]:
                raise cli.SessionError("invalid_parameter", "Choose one of the native Pi options")
            response = {"value": value}
    runtime.rpc.ui_reply(rid, **response)
    with runtime.events_lock:
        runtime.pending.pop(rid, None)
        runtime.persist()

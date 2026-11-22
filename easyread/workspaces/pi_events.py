"""Pi event lifecycle and bounded, public-only message projections."""
from __future__ import annotations

import secrets
import time

from . import pi_sessions, pi_requests, session_cli as cli
from .pi_inputs import public_native_text


def display_turn(runtime):
    # Native extensions can emit assistant/tool activity without a GUI receipt.
    # Such activity still needs an opaque display key, never a prompt-derived ID.
    if runtime.display_turn_id is None:
        runtime.display_turn_id = secrets.token_hex(12)
    return runtime.display_turn_id


def activity(runtime, events):
    for event in events:
        turn_id = runtime.tool_turn_ids.pop(event["id"], None) if event["kind"] == "tool_result" else None
        turn_id = turn_id or display_turn(runtime)
        if event["kind"] == "tool":
            if len(runtime.tool_turn_ids) >= 1024:
                raise ValueError("Pi tool display associations exceeded the limit")
            runtime.tool_turn_ids[event["id"]] = turn_id
        yield {**event, "at": cli.now_iso(), "turn_id": turn_id}


def handle(runtime, event):
    kind = event["type"]
    if kind in ("agent_start", "agent_settled", "compaction_start", "compaction_end", "queue_update"):
        runtime.lifecycle_cursor = max(runtime.lifecycle_cursor, event.get("_notification_cursor", 0))
    runtime.last_activity = time.monotonic()
    if kind == "extension_ui_request":
        pi_requests.receive(runtime, event)
        runtime.persist()
        return
    if kind == "queue_update":
        runtime.inputs.update_queue(event)
    elif kind == "agent_start":
        runtime.active_turn = runtime.active_turn or secrets.token_hex(12)
        runtime.phase = "running"
        runtime.settled = False
    elif kind == "agent_end":
        runtime.phase = "retrying" if event.get("willRetry") else "settling"
    elif kind == "agent_settled":
        runtime.flush_messages()
        runtime.active_turn = None
        runtime.display_turn_id = None
        runtime.settled = True
        runtime.phase = "idle"
        if event.get("aborted"):
            runtime.error = runtime.error or "Pi run aborted"
        runtime.check_identity_async()
    elif kind in ("auto_retry_start", "summarization_retry_scheduled", "summarization_retry_attempt_start"):
        runtime.phase = "retrying"
    elif kind in ("auto_retry_end", "summarization_retry_finished"):
        runtime.phase = "settling"
    elif kind == "compaction_start":
        runtime.compacting = True
        runtime.phase = "compacting"
    elif kind == "compaction_end":
        runtime.compacting = False
        runtime.phase = "settling" if not runtime.settled else "idle"
    elif kind == "extension_error":
        runtime.notice(str(event.get("error") or "Pi extension failed")[:2000])
    elif kind == "message_start":
        message = event.get("message") or {}
        if message.get("role") == "user":
            matched = runtime.inputs.delivered(message)
            runtime.display_turn_id = matched or secrets.token_hex(12)
            if not matched:
                text = public_native_text(pi_sessions.text_content(message.get("content")))
                if text:
                    runtime.mutate(lambda r: r["messages"].append({"id": runtime.display_turn_id, "role": "user",
                        "turn_id": runtime.display_turn_id, "content": text, "at": cli.now_iso(), "native": True}))
        elif message.get("role") == "assistant":
            runtime.flush_messages()
            runtime.message_id = "pi:" + runtime.generation + ":" + secrets.token_hex(12)
            runtime.message_turn_id = display_turn(runtime)
            runtime.message_partial = True
            runtime.adapter.completed = ""
            runtime.stream.text = ""
            runtime.error = ""
    # Existing Pi adaptation owns text block reconstruction, tools and usage;
    # only the RPC lifecycle owns completion (never last text or agent_end).
    if kind != "agent_settled":
        events = runtime.adapter.feed(runtime.stream, event)
        events = [e for e in events if e["kind"] != "text"]
        if events:
            tagged = list(activity(runtime, events))
            runtime.mutate(lambda r: r["events"].extend(tagged))
        if kind == "message_end" and (event.get("message") or {}).get("role") == "assistant":
            runtime.message_partial = False
            runtime.error = runtime.stream.error
            runtime.flush_messages()
        elif kind == "message_update":
            if len(runtime.stream.text) > 8 * 1024 * 1024:
                raise ValueError("Pi assistant message exceeded projection limit")
            if time.monotonic() - runtime.last_flush >= .3:
                runtime.flush_messages()
            return
    runtime.persist()


def flush(runtime):
    if not runtime.message_id or not runtime.stream.text:
        return
    text, mid, partial = runtime.stream.text, runtime.message_id, runtime.message_partial
    if (mid, text, partial) == runtime.last_projection:
        return
    def apply(record):
        for index, start in enumerate(range(0, len(text), 200000)):
            identity = mid + ":" + str(index)
            message = next((m for m in record["messages"] if m.get("id") == identity), None)
            if message is None:
                message = runtime.owner.sessions._journal(runtime.pid).message(runtime.sid, identity) or {
                    "id": identity, "role": "assistant", "at": cli.now_iso(), "turn_id": runtime.message_turn_id}
                record["messages"].append(message)
            message["content"] = text[start:start + 200000]
            if partial:
                message["partial"] = True
            else:
                message.pop("partial", None)
    runtime.mutate(apply)
    runtime.last_projection = (mid, text, partial)
    runtime.last_flush = time.monotonic()

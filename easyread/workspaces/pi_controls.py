"""Explicit Pi operations. No RPC preflight acknowledgement holds an HTTP lock."""
from __future__ import annotations

import base64
from copy import deepcopy
import re
import secrets
import threading
import time

from . import files, pi_protocol, pi_requests, session_cli as cli, session_images
from .pi_inputs import MAX_INPUTS, TERMINAL

# These names change the writer's identity or exist only in the native TUI.
IDENTITY_COMMANDS = {"new", "new-session", "reset", "resume", "switch", "fork", "clone", "tree", "cd", "cwd"}
CONTROL_COMMANDS = {"compact"}


def catalog(runtime):
    cached = runtime.commands_cache
    if cached and time.monotonic() - cached[0] < 60:
        return deepcopy(cached[1])
    result = runtime.rpc.call("get_commands")
    commands = result.get("commands")
    if not isinstance(commands, list) or len(commands) > 2048:
        raise cli.SessionError("native_rpc_error", "Pi command inventory is invalid or too large", 409)
    public = []
    for entry in commands:
        if (not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
                or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", entry["name"])
                or entry.get("source") not in ("extension", "prompt", "skill")):
            raise cli.SessionError("native_rpc_error", "Pi returned an invalid command", 409)
        public.append({"name": entry["name"], "description": str(entry.get("description") or "")[:2000],
                       "source": entry["source"]})
    runtime.commands_cache = (time.monotonic(), public)
    return deepcopy(public)


def prepare_prompt(runtime, prompt):
    prompt = prompt.lstrip() if prompt.lstrip().startswith(("/", "$")) else prompt
    match = re.match(r"^\$([A-Za-z0-9][A-Za-z0-9_.:-]*)(?=\s|$)", prompt)
    if match:
        name = "skill:" + match[1]
        commands = catalog(runtime)
        if any(c["name"] == name and c["source"] == "skill" for c in commands):
            prompt = "/" + name + prompt[match.end():]
    if not prompt.startswith("/"):
        return prompt, False
    name = prompt.split(None, 1)[0][1:]
    if name in IDENTITY_COMMANDS:
        raise cli.SessionError("command_unsupported", "Session identity/navigation commands require the native terminal", 409)
    commands = [c for c in catalog(runtime) if c["name"] == name]
    if not commands:
        raise cli.SessionError("command_unsupported", "This command is not in Pi's native inventory; use native controls or the terminal", 409)
    return prompt, any(c["source"] == "extension" for c in commands)


def failed_input(runtime, rid, error):
    runtime.inputs.items[rid].update(status="failed", error=str(error)[:2000],
                                    error_code=getattr(error, "code", "native_error"))
    if runtime.inputs.inflight == rid:
        runtime.inputs.inflight = None
    runtime.error = str(error)[:2000]
    if runtime.fenced:
        runtime.check_identity_async()
    runtime.persist()


def submit(owner, pid, sid, body, message, prompt, references, images=None, kind="send"):
    images = images or []
    if owner._record(pid, sid)["engine"] != "pi":
        raise cli.SessionError("capability_unavailable", "Native follow-up requires Pi RPC", 409)
    with owner.use(pid, sid, existing=kind != "send") as runtime:
        if getattr(runtime, "engine", None) != "pi":
            raise cli.SessionError("capability_unavailable", "Native follow-up requires Pi RPC", 409)
        with runtime.events_lock:
            if runtime.submission_pending or runtime.identity_checks or runtime.fenced:
                raise cli.SessionError("submission_pending", "Wait for Pi to acknowledge the previous submission", 409)
            if runtime.compacting:
                raise cli.SessionError("conflict", "Pi is compacting; wait for native completion", 409)
            expected = runtime.active_turn
            if kind != "send" and body.get("generation", runtime.generation) != runtime.generation:
                raise cli.SessionError("stale_generation", "This Pi runtime generation has changed", 409)
            if kind == "steer":
                expected = body.get("expected_turn_id", runtime.active_turn)
                if not runtime.active_turn or expected != runtime.active_turn:
                    raise cli.SessionError("stale_turn", "The observed Pi run is no longer active", 409)
            elif kind == "send" and (not runtime.settled or runtime.pending or runtime.pending_inputs() or any(runtime.inputs.queues.values())) and not message.lstrip().startswith("/"):
                raise cli.SessionError("conflict", "Pi has active native work; use steer or follow-up", 409)
            if len(runtime.inputs.items) >= MAX_INPUTS and not any(i["status"] in TERMINAL or i.get("retired") for i in runtime.inputs.items.values()):
                raise cli.SessionError("queue_capacity", "Pi pending inputs are full", 409)
            rid = secrets.token_hex(12)
            item = {"id": rid, "kind": kind, "message": message, "files": references,
                    **({"images": images} if images else {}), "status": "submitting"}
            runtime.inputs.add(item)
            runtime.start_ack_wait()
            runtime.error = ""
            def journal(r):
                r["messages"].append({"id": rid, "role": "user", "content": message, "files": references,
                                      **({"images": images} if images else {}), "at": cli.now_iso(), "delivery_status": "submitting"})
            runtime.persist(journal)
        def send():
            sent = False
            try:
                # Only explicit submission/discovery loads resources. No browser
                # path is ever read for a Skill: native Pi expands verified names.
                wire, extension = prepare_prompt(runtime, prompt)
                payload = []
                for path in session_images.paths(runtime.cwd, images):
                    payload.append({"type": "image", "mimeType": "image/png",
                                    "data": base64.b64encode(files.read_bytes(path)).decode("ascii")})
                with runtime.events_lock:
                    if runtime.closed or runtime.uncertain or runtime.inputs.inflight != rid:
                        return
                    runtime.fenced = extension
                    if kind == "steer" and (not runtime.active_turn or expected != runtime.active_turn):
                        raise cli.SessionError("stale_turn", "Pi run ended before submission", 409)
                sent = True
                # Steering cannot accidentally start a fresh run. Follow-up is
                # deliberately atomic at Pi: queue while running, or start the
                # same-session continuation if settlement won the race.
                command_type = "steer" if kind == "steer" and not extension else "prompt"
                runtime.rpc.submit(rid, command_type, message=wire, images=payload,
                                   **({"streamingBehavior": "followUp"} if kind == "follow_up" else {}))
            except Exception as error:
                with runtime.events_lock:
                    if runtime.closed:
                        return
                    if sent and not isinstance(error, pi_protocol.UnwrittenError):
                        runtime.fail(str(error))
                    else:
                        failed_input(runtime, rid, error)
        threading.Thread(target=send, name="pi-submit-" + rid, daemon=True).start()
    return {**owner.sessions.get(pid, sid), "submission": {"id": rid, "status": "submitting"}}


def respond(owner, pid, sid, body):
    # Bypass use()/ensure(): prompt extensions can await UI before acknowledging.
    # Startup dialogs are explicitly rejected when RPC input is not initialized.
    owner._record(pid, sid)
    with owner.lock:
        runtime = owner.runtimes.get((pid, sid))
    if runtime is None or getattr(runtime, "engine", None) != "pi":
        raise cli.SessionError("request_expired", "No live Pi runtime owns this dialog", 409)
    # Replies are independent of session admission; they must not wait for the
    # prompt acknowledgement that the human response itself will unblock.
    with runtime.ui_operation:
        pi_requests.answer(runtime, body)
    return owner.sessions.get(pid, sid)


def clear_queue(runtime):
    if runtime.submission_pending:
        raise cli.SessionError("submission_pending", "Pi preflight is still outstanding; Stop cancels it", 409)
    with runtime.events_lock:
        runtime.inputs.clearing = True
    try:
        runtime.rpc.call("clear_queue")
    except cli.SessionError as error:
        runtime.fail(str(error))
        raise
    finally:
        with runtime.events_lock:
            runtime.inputs.clearing = False
            runtime.persist()


def cancel(runtime):
    # Clear BEFORE abort: Pi's abort can otherwise continue queued work. Outstanding
    # extension preflights may still enqueue later, so explicit Stop also releases
    # the process rather than claiming the entire preflight was aborted.
    if runtime.startup_ui_error:
        return  # Startup stdin is unavailable; explicit Stop closes the owned process.
    try:
        with runtime.events_lock:
            runtime.inputs.clearing = True
        runtime.rpc.call("clear_queue")
        for rid in list(runtime.pending):
            runtime.rpc.ui_reply(rid, cancelled=True)
        runtime.rpc.call("abort")
    except cli.SessionError:
        pass  # Explicit Stop still authorizes process shutdown, never replay.


def command(owner, pid, sid, body):
    name, argument = body.get("command"), body.get("arguments", "")
    if not isinstance(name, str) or name not in CONTROL_COMMANDS:
        raise cli.SessionError("command_unsupported", "Only the native compact control is supported", 409)
    if not isinstance(argument, str) or len(argument) > 200000:
        raise cli.SessionError("invalid_parameter", "Command arguments must be text of at most 200000 characters")
    if owner._record(pid, sid)["engine"] != "pi":
        raise cli.SessionError("capability_unavailable", "This native control requires Pi", 409)
    with owner.use(pid, sid) as runtime:
        if getattr(runtime, "engine", None) != "pi":
            raise cli.SessionError("capability_unavailable", "This native control requires Pi", 409)
        if runtime.busy() and (runtime.operation_count > 1 or runtime.active_turn or not runtime.settled
                              or runtime.pending or runtime.submission_pending or runtime.identity_checks
                              or runtime.compacting or runtime.fenced or any(runtime.inputs.queues.values())):
            raise cli.SessionError("conflict", "Wait for Pi's current work to settle", 409)
        rid = secrets.token_hex(12)
        with runtime.events_lock:
            runtime.inputs.add({"id": rid, "kind": "command", "message": "/compact" + (" " + argument if argument else ""), "status": "submitting"})
            runtime.start_ack_wait()
            runtime.persist()
        def execute():
            try:
                runtime.rpc.submit(rid, "compact", **({"customInstructions": argument} if argument else {}))
            except Exception as error:
                with runtime.events_lock:
                    if not runtime.closed:
                        if isinstance(error, pi_protocol.UnwrittenError):
                            failed_input(runtime, rid, error)
                        else:
                            runtime.fail(str(error))
        threading.Thread(target=execute, name="pi-compact", daemon=True).start()
    return {**owner.sessions.get(pid, sid), "submission": {"id": rid, "status": "submitting"}}

"""Serialized session settings and exclusive GUI/native-terminal handoff.

Admission is separate from the project lock: native shutdown may wait for its
notification consumer to persist state under that lock. Readers stay available.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import threading

from . import session_cli as cli, session_reasoning as reasoning, session_terminal as term
from .codex_runtime import RETAIN_GOALS


@contextmanager
def operation(sessions, pid, sid):
    key = (pid, sid)
    with sessions._lock:
        lock, users = sessions._operations.get(key, (threading.RLock(), 0))
        sessions._operations[key] = (lock, users + 1)
    acquired = lock.acquire(blocking=False)
    try:
        if not acquired:
            raise cli.SessionError("conflict", "Another session operation is in progress; retry when it finishes", 409)
        yield
    finally:
        if acquired:
            lock.release()
        with sessions._lock:
            _, users = sessions._operations[key]
            if users == 1:
                del sessions._operations[key]
            else:
                sessions._operations[key] = (lock, users - 1)


def serialized(method):
    @wraps(method)
    def call(self, pid, sid, *args, **kwargs):
        with operation(self, pid, sid):
            return method(self, pid, sid, *args, **kwargs)
    return call


def require_native_sync(record):
    if record.get("native_sync_warning"):
        raise cli.SessionError("reconciliation_required", record["native_sync_warning"]["message"], 409)


def require_idle(sessions, pid, sid, record):
    # Durable flags also protect work left for reconciliation after a restart.
    if (sessions._closed or (pid, sid) in sessions._jobs or sessions.native.busy(pid, sid)
            or sessions.terminals.scope_active(pid, sid)
            or record.get("state") in ("queued", "running", "waiting_user", "waiting_external")
            or record.get("runtime_state") == "starting"
            or any(record.get(key) for key in ("active_turn_id", "native_active", "background_active", "queued"))
            or any(not request.get("expired") for request in record.get("pending_requests", []))
            or (record.get("goal") or {}).get("status") in RETAIN_GOALS):
        raise cli.SessionError("conflict", "Session still owns native work; stop or reconcile it first", 409)


@serialized
def settings(sessions, pid, sid, body):
    sessions.store.get(pid)
    sessions._check_sid(sid)
    if not isinstance(body, dict) or set(body) - {"model", "reasoning_effort"}:
        raise cli.SessionError("invalid_parameter", "Settings accept only model and reasoning_effort")
    with sessions.store.lock(pid), sessions._lock:
        record = sessions._record(sessions._load(pid), sid)
        require_idle(sessions, pid, sid, record)
        engine = record["engine"]
        model = sessions._model(body["model"], engine) if "model" in body else record.get("model", "")
        value = body.get("reasoning_effort", record.get("reasoning_effort", ""))
    value = reasoning.validate(sessions.store, pid, engine, model, value)
    # Hold only admission across the wait, not project/manager locks.
    sessions.native.stop(pid, sid, idle_only=True)
    with sessions.store.lock(pid), sessions._lock:
        data = sessions._load(pid)
        record = sessions._record(data, sid)
        require_idle(sessions, pid, sid, record)
        record.update(model=model, reasoning_effort=value, updated=cli.now_iso())
        record.pop("capability_mode", None)
        sessions._write(pid, data)
    return sessions.get(pid, sid)


@serialized
def terminal(sessions, pid, sid, access=None, access_sid=None):
    sessions.store.get(pid)
    sessions._check_sid(sid)
    if access is not None:
        sessions.terminals.access = access
    with sessions.store.lock(pid), sessions._lock:
        data = sessions._load(pid)
        record = sessions._record(data, sid)
        try:
            existing = sessions.terminals.scope_url(pid, sid, access_sid)
        except ValueError:
            raise cli.SessionError("conflict", "Native terminal belongs to another access session", 409) from None
        if existing:
            return {"url": existing, "engine": record["engine"], "native_id": record.get("native_id", "")}
        require_native_sync(record)
        require_idle(sessions, pid, sid, record)
    reason = term.unavailable_reason()
    if reason:
        raise cli.SessionError("terminal_unavailable", reason, 409)
    sessions.native.stop(pid, sid, idle_only=True)
    with sessions.store.lock(pid), sessions._lock:
        data = sessions._load(pid)
        record = sessions._record(data, sid)
        require_native_sync(record)
        require_idle(sessions, pid, sid, record)
        engine = sessions._engine(record.get("engine"))
        root = term.workspace_cwd(sessions.store.root(pid))
        env, cli_home, isolated = cli.launch_env(Path(sessions.store.meta(pid)), engine, record.get("cli_isolated"))
        native_id = record.get("native_id") or ""
        path = cli.transcript(engine, cli_home, native_id, root) if native_id else None
        resume = bool(path)
        if native_id and not path and (record.get("resumed") or record.get("native_started")
                                       or any(m["role"] == "assistant" for m in record["messages"])):
            raise cli.SessionError("not_found", "Native transcript is missing; refusing to start an unrelated conversation", 409)
        model, effort = record.get("model", ""), record.get("reasoning_effort", "")
    if engine == "codex" and resume and (not model or not effort):
        from .codex_model_list import defaults
        model, effort = defaults(cli.executable(engine), cli_home, root, env, model, effort)
    with sessions.store.lock(pid), sessions._lock:
        data = sessions._load(pid)
        record = sessions._record(data, sid)
        require_native_sync(record)
        require_idle(sessions, pid, sid, record)
        if any(r.get("engine") == engine and r.get("terminal_tracking") and not r.get("native_id")
               and sessions.terminals.scope_active(pid, r["id"]) for r in data["sessions"].summaries()):
            raise cli.SessionError("conflict", "Wait for the new native terminal to record its session ID", 409)
        if engine == "codex" and not resume and (sessions.native.busy(pid) or any(key[0] == pid for key in sessions._jobs)):
            raise cli.SessionError("conflict", "Wait for active turns before opening a fresh native terminal", 409)
        argv = cli.terminal_argv(engine, native_id, model, resume=resume,
                                 libroot=Path(sessions.store.libroot), pid=pid, reasoning_effort=effort,
                                 cli_home=cli_home, root=root)
        # Pi allocates its UUID before launch; unrelated files are not candidates.
        tracking = {"baseline": [] if engine == "pi" and native_id else list(cli.project_transcripts(engine, cli_home, root)),
                    "message_count": len(cli.transcript_messages(engine, cli.read_transcript(path))) if path else 0}
        try:
            result = sessions.terminals.open_native(access_sid=access_sid, scope=(pid, sid), argv=argv, cwd=root, env=env)
        except ValueError as error:
            raise cli.SessionError("terminal_unavailable", str(error), 409) from None
        record["terminal_tracking"] = tracking
        record["cli_isolated"] = isolated
        sessions._write(pid, data)
    return {**result, "engine": engine, "isolated": isolated, "native_id": native_id, "command": argv[1:]}

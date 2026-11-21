"""Admission, explicit operations, and idle-only lifetime of Codex runtimes."""
from __future__ import annotations

from contextlib import contextmanager
import os
import threading
import time
import secrets

from . import codex_protocol as protocol, codex_requests as requests, session_cli as cli
from .codex_runtime import Runtime, RETAIN_GOALS, state


def setting(name, default, minimum=1):
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except ValueError:
        return default


class CodexSessions:
    def __init__(self, sessions):
        self.sessions = sessions
        self.lock = threading.RLock()
        self.runtimes = {}
        self.max_active = setting("EASYREAD_WORKSPACE_MAX_ACTIVE", 4)
        self.max_runtimes = setting("EASYREAD_WORKSPACE_MAX_RUNTIMES", 8)
        self.idle_grace = setting("EASYREAD_WORKSPACE_IDLE_GRACE", 300)
        self.stop_event = threading.Event()
        self.reaper = None

    def count_active(self):
        with self.lock:
            return sum(r.busy() for r in self.runtimes.values())

    def busy(self, pid=None, sid=None):
        with self.lock:
            return any((pid is None or p == pid) and (sid is None or s == sid) and runtime.busy()
                       for (p, s), runtime in self.runtimes.items())

    def loaded(self, pid=None, sid=None):
        with self.lock:
            return any((pid is None or p == pid) and (sid is None or s == sid) for p, s in self.runtimes)

    def status(self):
        with self.lock:
            return {"active": self.count_active(), "loaded": len(self.runtimes), "max_active": self.max_active,
                    "max_runtimes": self.max_runtimes, "idle_grace_seconds": self.idle_grace}

    def _record(self, pid, sid):
        self.sessions.store.get(pid)
        self.sessions._check_sid(sid)
        with self.sessions.store.lock(pid):
            data = self.sessions._load(pid)
            record = self.sessions._record(data, sid)
            if record["engine"] != "codex":
                raise cli.SessionError("capability_unavailable", "Claude exec does not support native goals, steer, or user input", 409)
            if self.sessions.terminals.scope_active(pid, sid):
                raise cli.SessionError("conflict", "Close this session's native terminal first", 409)
            if any(r.get("engine") == "codex" and r.get("terminal_tracking") and not r.get("native_id")
                   for r in data["sessions"].summaries()):
                raise cli.SessionError("conflict", "Wait for the new native terminal to record its session ID", 409)
            return record

    @contextmanager
    def use(self, pid, sid, *, existing=False, reconcile=False):
        with self.sessions.operation(pid, sid):
            with self._use(pid, sid, existing=existing, reconcile=reconcile) as runtime:
                yield runtime

    @contextmanager
    def _use(self, pid, sid, *, existing=False, reconcile=False):
        record = self._record(pid, sid)
        if (record.get("reconciliation_required") and not reconcile
                and (record.get("goal") or {}).get("status") in RETAIN_GOALS):
            raise cli.SessionError("reconciliation_required", "Native work was interrupted. Reconcile external work, then explicitly resume or clear the goal", 409)
        with self.sessions._lock, self.lock:
            if self.sessions._closed:
                raise cli.SessionError("unavailable", "Service is shutting down", 409)
            runtime = self.runtimes.get((pid, sid))
            if runtime is None:
                if existing:
                    raise cli.SessionError("runtime_expired", "No live runtime owns this request or turn", 409)
                if len(self.runtimes) >= self.max_runtimes:
                    raise cli.SessionError("runtime_capacity", "Native runtime capacity reached; wait for idle reclamation", 429)
                self.sessions._prune_jobs()
                if self.count_active() + len(self.sessions._jobs) >= self.max_active:
                    raise cli.SessionError("active_capacity", "Active session capacity reached; pause or stop other work", 429)
                runtime = Runtime(self, pid, sid)
                self.runtimes[(pid, sid)] = runtime
                if self.reaper is None:
                    self.reaper = threading.Thread(target=self._maintain, name="workspace-runtime-reaper", daemon=True)
                    self.reaper.start()
            elif not runtime.busy():
                if self.count_active() + len(self.sessions._jobs) >= self.max_active:
                    raise cli.SessionError("active_capacity", "Active session capacity reached", 429)
            with runtime.lock:
                runtime.operation_count += 1
        try:
            with runtime.operation:
                with self.lock:
                    if self.runtimes.get((pid, sid)) is not runtime:
                        raise cli.SessionError("conflict", "Native runtime changed; retry this operation", 409)
                # Re-read after serialization: another HTTP operation may have changed state.
                record = self._record(pid, sid)
                if runtime.uncertain and not reconcile:
                    raise cli.SessionError("reconciliation_required", "The last native operation has an uncertain outcome; cancel before retrying", 409)
                runtime.ensure(record)
                yield runtime
        except Exception as error:
            if runtime.rpc is None:
                with self.lock:
                    if self.runtimes.get((pid, sid)) is runtime:
                        self.runtimes.pop((pid, sid))
            elif runtime.starting:
                runtime.failure = str(error)
                runtime.uncertain = True
                runtime.mutate(lambda r: r.update(state="interrupted", reconciliation_required=True, error=str(error)))
            elif isinstance(error, cli.SessionError) and error.code in ("rpc_uncertain", "runtime_expired"):
                runtime.uncertain = True
                runtime.mutate(lambda r: r.update(state="interrupted", reconciliation_required=True, error=str(error)))
            raise
        finally:
            with runtime.lock:
                runtime.operation_count -= 1
                runtime.last_activity = time.monotonic()

    def send(self, pid, sid, message, prompt, references):
        with self.use(pid, sid) as runtime:
            with runtime.lock:
                if (runtime.submission_pending or runtime.active_turn or runtime.native_active
                        or runtime.pending or runtime.tools or runtime.queued
                        or (runtime.goal or {}).get("status") in RETAIN_GOALS):
                    raise cli.SessionError("conflict", "This session has active native work; steer it or pause the goal first", 409)
            inputs = runtime.inputs(prompt)
            with runtime.lock:
                # RPC replies can overtake notification persistence. Keep admission
                # until turn/started establishes the accepted native lifecycle.
                runtime.submission_pending = True
            mid = secrets.token_hex(12)
            def submit(r):
                r["messages"].append({"id": mid, "role": "user", "content": message, "files": references, "at": cli.now_iso()})
                r.update(state="queued", error="", reconciliation_required=False)
            runtime.mutate(submit)
            try:
                runtime.call("turn/start", {"threadId": runtime.thread_id, "input": inputs,
                                            "clientUserMessageId": mid})
            except cli.SessionError as error:
                with runtime.lock:
                    if not runtime.uncertain:
                        runtime.submission_pending = False
                runtime.mutate(lambda r: r.update(state="interrupted" if runtime.uncertain else "error", error=str(error)))
                raise
        return self.sessions.get(pid, sid)

    def goal(self, pid, sid, body):
        action = body.get("action")
        if action not in ("set", "pause", "resume", "clear"):
            raise cli.SessionError("invalid_parameter", "Goal action must be set, pause, resume, or clear")
        objective = body.get("objective")
        if action == "set" and (not isinstance(objective, str) or not objective.strip() or len(objective) > 200000):
            raise cli.SessionError("invalid_parameter", "A non-empty goal objective of at most 200000 characters is required")
        with self.use(pid, sid, reconcile=True) as runtime:
            if runtime.uncertain:
                raise cli.SessionError("reconciliation_required", "Cancel the uncertain runtime before changing its goal", 409)
            runtime.set_goal(action, objective)
        return self.sessions.get(pid, sid)

    def steer(self, pid, sid, body, message, prompt, references):
        with self.use(pid, sid, existing=True) as runtime:
            runtime.require("steer")
            with runtime.lock:
                turn_id = runtime.active_turn
            expected = body.get("expected_turn_id", turn_id)
            if not turn_id or expected != turn_id:
                raise cli.SessionError("stale_turn", "The observed native turn is no longer active", 409)
            mid = secrets.token_hex(12)
            runtime.call("turn/steer", {"threadId": runtime.thread_id, "expectedTurnId": expected,
                                        "input": runtime.inputs(prompt), "clientUserMessageId": mid})
            runtime.mutate(lambda r: r["messages"].append({"id": mid, "role": "user", "content": message,
                                                         "files": references, "turn_id": turn_id, "at": cli.now_iso(), "steer": True}))
        return self.sessions.get(pid, sid)

    def respond(self, pid, sid, body):
        key = protocol.request_key(body.get("request_id"))
        with self.use(pid, sid, existing=True) as runtime:
            with runtime.lock:
                pending = runtime.pending.get(key)
                if (body.get("generation") != runtime.generation or not pending or runtime.closed
                        or pending[0]["thread_id"] not in {runtime.thread_id, *runtime.children}):
                    raise cli.SessionError("request_expired", "This native request has expired or belongs to a different runtime", 409)
                card, method, params, _ = pending
                answer = requests.response(method, params, body)
                runtime.rpc.reply(card["id"], answer)
                runtime.resolve_request(card["id"])
            def replied(r):
                r["pending_requests"] = [p for p in r.get("pending_requests", [])
                                         if not (protocol.request_key(p["id"]) == key and p["generation"] == runtime.generation)]
                r["state"] = state(r)
                r["events"].append({"kind": "response", "at": cli.now_iso(), "request_id": card["id"], "turn_id": card.get("turn_id")})
            runtime.mutate(replied)
        return self.sessions.get(pid, sid)

    def stop(self, pid, sid, *, cancel=False, idle_only=False):
        with self.lock:
            runtime = self.runtimes.get((pid, sid))
        if runtime is None:
            return
        with runtime.operation:
            with self.lock:
                if self.runtimes.get((pid, sid)) is not runtime:
                    return
            # The caller's earlier idle check is advisory: a send may have won
            # this operation lock in the meantime. Never kill that new work.
            if idle_only and runtime.busy():
                raise cli.SessionError("conflict", "Native session still owns active work", 409)
            if cancel and runtime.rpc and not runtime.closed:
                try:
                    # Pause the scheduler before interrupting; otherwise a goal can start another turn.
                    if (runtime.goal or {}).get("status") in RETAIN_GOALS and runtime.capabilities.get("goal") and not runtime.uncertain:
                        runtime.set_goal("pause")
                    if runtime.active_turn and not runtime.uncertain:
                        runtime.call("turn/interrupt", {"threadId": runtime.thread_id, "turnId": runtime.active_turn})
                except cli.SessionError:
                    # Explicit cancellation still authorizes process shutdown when
                    # native reconciliation fails. Failed persistence stays pinned.
                    pass
            runtime.stop()  # Explicit cancellation may terminate background tools, unlike idle reclamation.
        with self.lock:
            if self.runtimes.get((pid, sid)) is runtime:
                self.runtimes.pop((pid, sid))

    def sleep_project(self, pid):
        """Release proven-idle native DB writers before archive/terminal/delete."""
        with self.lock:
            keys = [key for key in self.runtimes if key[0] == pid]
        for p, sid in keys:
            self.stop(p, sid, idle_only=True)

    def reap(self):
        with self.lock:
            runtimes = list(self.runtimes.items())
        for (pid, sid), runtime in runtimes:
            if not runtime.operation.acquire(blocking=False):
                continue
            try:
                runtime.flush_messages()
                if runtime.closed or runtime.uncertain or runtime.starting:
                    continue
                if not runtime.submission_pending and not runtime.active_turn and not runtime.pending and not runtime.tools:
                    runtime.refresh_background()
                if not runtime.busy() and time.monotonic() - runtime.last_activity >= self.idle_grace:
                    self.stop(pid, sid, idle_only=True)
            except (cli.SessionError, OSError):
                # Failed liveness probes are never permission to kill an experiment.
                continue
            finally:
                runtime.operation.release()

    def _maintain(self):
        while not self.stop_event.wait(2):
            self.reap()

    def close(self):
        self.stop_event.set()
        if self.reaper:
            self.reaper.join(35)
        with self.lock:
            keys = list(self.runtimes)
        errors = []
        for pid, sid in keys:
            try:
                self.stop(pid, sid)
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]

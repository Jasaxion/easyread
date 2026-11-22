"""Resident Pi runtime; a process is not a turn and a metadata idle is not settled."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import secrets
import threading
import time

from . import pi_events, pi_inputs, pi_protocol, pi_requests, pi_sessions, session_cli as cli
from .session_store import expire

ACK_TIMEOUT = 60


class Runtime:
    engine = "pi"

    def __init__(self, owner, pid, sid):
        self.owner, self.pid, self.sid = owner, pid, sid
        self.operation = threading.RLock()
        self.lock = threading.RLock()
        self.events_lock = threading.RLock()
        self.ui_operation = threading.Lock()
        self.rpc = None
        self.generation = ""
        self.operation_count = 0
        self.closed = False
        self.starting = True
        self.uncertain = False
        self.persistence_failed = False
        self.failure = ""
        self.error = ""
        self.active_turn = None
        self.display_turn_id = None
        self.tool_turn_ids = {}
        self.compacting = False
        self.settled = True
        self.phase = "starting"
        self.pending = {}
        self.inputs = pi_inputs.Inputs()
        self.last_activity = time.monotonic()
        self.submitted_at = 0
        self.ack_paused_at = None
        self.ack_run_busy = False
        self.ack_compacting = False
        self.startup_ui_error = ""
        self.identity_checks = 0
        self.fenced = False
        self.buffered = []
        self.buffered_bytes = 0
        self.commands_cache = None
        self.native_id = ""
        self.session_file = None
        self.stream = cli.Stream("pi")
        self.adapter = pi_sessions.Events()
        self.message_id = None
        self.message_turn_id = None
        self.message_partial = True
        self.last_projection = None
        self.last_flush = 0
        self.ready = threading.Event()
        self.lifecycle_cursor = 0
        self.saved_receipts = {}

    @property
    def submission_pending(self):
        return self.inputs.inflight is not None

    def pending_inputs(self):
        return any(item["status"] in pi_inputs.PENDING and not item.get("retired")
                   for item in list(self.inputs.items.values()))

    def busy(self):
        # Called under manager admission locks; never acquire a persistence lock.
        return bool(self.operation_count or self.starting or self.uncertain or self.submission_pending
                    or self.identity_checks or self.fenced or self.active_turn or not self.settled
                    or self.compacting or self.pending or any(self.inputs.queues.values()) or self.pending_inputs())

    def mutate(self, action):
        def apply(data):
            record = self.owner.sessions._record(data, self.sid)
            action(record)
            record["updated"] = cli.now_iso()
        try:
            self.owner.sessions._commit(self.pid, apply)
        except Exception:
            self.persistence_failed = self.uncertain = True
            raise

    def persist(self, extra=None):
        self.update_ack_pause()
        statuses = {rid: item["status"] for rid, item in self.inputs.items.items()}
        def apply(r):
            if extra:
                extra(r)
            present = {message.get("id") for message in r["messages"]}
            for rid, status in statuses.items():
                if rid not in present and self.saved_receipts.get(rid) != status:
                    message = self.owner.sessions._journal(self.pid).message(self.sid, rid)
                    if message:
                        r["messages"].append(message)
            self.inputs.project(r)
            phase = "interrupted" if self.uncertain else "waiting_user" if self.pending else self.phase
            if not self.uncertain and not self.pending and self.submission_pending and self.phase == "idle":
                phase = "submitting"
            r.update(runtime_state="sleeping" if self.closed else "starting" if self.starting else "awake",
                     runtime_phase=phase, active_turn_id=self.active_turn, native_active=not self.settled,
                     active_display_turn_id=self.display_turn_id if self.active_turn or not self.settled or self.compacting else None,
                     generation=self.generation, pending_requests=[deepcopy(p[0]) for p in self.pending.values()],
                     queued=any(self.inputs.queues.values()), submission_pending=self.submission_pending,
                     capabilities=dict(pi_sessions.CAPABILITIES),
                     error=self.error, reconciliation_required=self.uncertain)
            r["state"] = ("interrupted" if self.uncertain else "waiting_user" if self.pending else
                          "running" if self.active_turn or not self.settled or self.compacting else
                          "queued" if self.submission_pending or self.pending_inputs() or any(self.inputs.queues.values()) or self.identity_checks else
                          "error" if self.error else "idle")
            if self.stream.usage:
                r["usage"] = self.stream.usage
            if self.stream.model:
                r["reported_model"] = self.stream.model
            if self.stream.reasoning_effort:
                r["reported_reasoning_effort"] = self.stream.reasoning_effort
        self.mutate(apply)
        self.saved_receipts = statuses

    def ensure(self, record):
        if self.rpc:
            if self.closed or self.starting:
                raise cli.SessionError("runtime_expired", "Pi runtime disconnected; stop before explicitly resuming", 409)
            return
        from .session_controls import require_native_sync
        require_native_sync(record)
        from .pi_recovery import unowned, UNOWNED
        if unowned(record):
            raise cli.SessionError("reconciliation_required", UNOWNED, 409)
        exe = cli.executable("pi")
        if not exe:
            raise cli.SessionError("engine_missing", "Pi CLI is not installed", 409)
        if not pi_sessions.capabilities()["steer"]:
            raise cli.SessionError("capability_unavailable", "Pi 1.6+ RPC with agent_settled is required", 409)
        sessions = self.owner.sessions
        self.cwd = Path(sessions.store.root(self.pid))
        env, self.home, _ = cli.launch_env(Path(sessions.store.meta(self.pid)), "pi", record.get("cli_isolated"))
        self.native_id = record.get("native_id") or cli.new_native_id("pi")
        path = pi_sessions.transcript(self.home, self.native_id, self.cwd)
        if not path and (record.get("native_started") or record.get("resumed")
                         or any(m["role"] == "assistant" for m in record["messages"])):
            raise cli.SessionError("not_found", "Pi transcript is missing; refusing an unrelated conversation", 409)
        self.inputs.items = {item["id"]: deepcopy(item) for item in record.get("pending_inputs", [])}
        self.session_file = path.resolve() if path else None
        argv = pi_sessions.argv(exe=exe, native_id=self.native_id, resume=bool(path), cli_home=self.home,
                                cwd=self.cwd, selected_model=record.get("model", ""),
                                reasoning_effort=record.get("reasoning_effort", ""), rpc=True)
        self.generation = secrets.token_hex(16)
        # Recheck the current journal under the project lock, not just the
        # earlier admission snapshot. Persist before any native writer can exist.
        def reserve(current):
            if unowned(current) or current.get("reconciliation_required"):
                raise cli.SessionError("reconciliation_required", UNOWNED, 409)
            current.update(native_id=self.native_id, runtime_state="starting", generation=self.generation)
        self.mutate(reserve)
        try:
            self.rpc = pi_protocol.PiRPC(argv, self.cwd, env, self.on_message, self.on_exit, generation=self.generation)
        except pi_protocol.UnstartedError as error:
            self.mutate(lambda r: r.update(runtime_state="sleeping", runtime_released_generation=self.generation,
                                           state="error", error=str(error)))
            raise
        self.ready.set()
        state = self.rpc.call("get_state")
        with self.events_lock:
            self.validate_identity(state)
            self.starting = False
            if state.get("isStreaming") or state.get("isCompacting") or state.get("pendingMessageCount"):
                self.settled = False
                self.phase = "settling"
            else:
                self.phase = "idle"
            self.persist()

    def validate_identity(self, state):
        name = state.get("sessionFile")
        path = Path(name).resolve() if isinstance(name, str) and name else None
        if (state.get("sessionId") != self.native_id or path is None
                or path.parent != (self.home / "sessions").resolve()
                or not path.name.endswith("_" + self.native_id + ".jsonl")
                or (self.session_file is not None and path != self.session_file)):
            raise cli.SessionError("native_conflict", "Pi changed native session identity; this GUI record was not retargeted", 409)
        if path.exists():
            snapshot = pi_sessions.read_snapshot(path, self.native_id, live=True)
            if Path(snapshot.rows[0]["cwd"]).resolve() != self.cwd.resolve():
                raise cli.SessionError("native_conflict", "Pi changed working directory; reconcile in the native terminal", 409)
            self.mutate(lambda r: r.update(native_started=True))
        self.session_file = path

    def check_identity_async(self):
        if self.closed:
            return
        self.identity_checks += 1
        def check():
            try:
                state = self.rpc.call("get_state")
                with self.events_lock:
                    if self.closed:
                        return
                    self.validate_identity(state)
                    # Idle metadata cannot erase an un-settled run. Positive
                    # activity can, however, fence extension-started work.
                    if (state.get("_rpc_cursor", 0) >= self.lifecycle_cursor
                            and (state.get("isStreaming") or state.get("isCompacting") or state.get("pendingMessageCount"))):
                        self.settled = False
                    if self.fenced and not self.submission_pending:
                        self.fenced = False
                        buffered, self.buffered = self.buffered, []
                        self.buffered_bytes = 0
                        for event in buffered:
                            pi_events.handle(self, event)
            except Exception as error:
                self.fail(str(error))
            finally:
                with self.events_lock:
                    self.identity_checks -= 1
                    if not self.closed:
                        self.persist()
        threading.Thread(target=check, name="pi-identity-check", daemon=True).start()

    def fail(self, reason):
        with self.events_lock:
            if self.closed:
                return
            self.uncertain = True
            self.error = reason
            self.inputs.interrupt(reason)
            self.persist()

    def on_message(self, event):
        self.ready.wait()
        with self.events_lock:
            if self.closed:
                return
            self.observe_ack_activity(event["type"])
            if event["type"] == "response":
                if event["id"] not in self.inputs.items:
                    return  # Late read-only query response; never replay a command.
                self.inputs.acknowledge(event)
                self.check_identity_async()
                self.persist()
            elif self.fenced and event["type"] != "extension_ui_request":
                self.buffered_bytes += len(json.dumps(event).encode())
                if self.buffered_bytes > 8 * 1024 * 1024:
                    raise ValueError("Pi command identity fence exceeded the event budget")
                self.buffered.append(event)
            else:
                pi_events.handle(self, event)

    def notice(self, text):
        self.mutate(lambda r: r["events"].append({"kind": "notice", "at": cli.now_iso(), "text": text}))

    def flush_messages(self):
        with self.events_lock:
            pi_events.flush(self)

    def start_ack_wait(self):
        self.submitted_at = time.monotonic()
        self.ack_paused_at = None
        self.update_ack_pause()

    def update_ack_pause(self, now=None):
        now = time.monotonic() if now is None else now
        if not self.submission_pending:
            self.ack_paused_at = None
            return
        blocked = bool(self.pending or self.ack_run_busy or self.ack_compacting or not self.settled or self.compacting)
        if blocked and self.ack_paused_at is None:
            self.ack_paused_at = now
        elif not blocked and self.ack_paused_at is not None:
            self.submitted_at += max(0, now - self.ack_paused_at)
            self.ack_paused_at = None

    def observe_ack_activity(self, kind):
        # Observe even identity-fenced events: they are evidence of a native wait,
        # not permission to publish output or change this record's identity.
        if kind in ("agent_start", "agent_end", "auto_retry_start", "auto_retry_end"):
            self.ack_run_busy = True
        elif kind == "agent_settled":
            self.ack_run_busy = False
        elif kind == "compaction_start":
            self.ack_compacting = True
        elif kind == "compaction_end":
            self.ack_compacting = False
        self.update_ack_pause()

    def maintain(self):
        with self.events_lock:
            self.flush_messages()
            self.update_ack_pause()
            if pi_requests.expire(self):
                self.persist()
            self.update_ack_pause()
            if (self.submission_pending and self.ack_paused_at is None and not self.uncertain
                    and time.monotonic() - self.submitted_at > ACK_TIMEOUT):
                self.fail("Pi acknowledgement is overdue; outcome unknown. Stop and reconcile before submitting again.")

    def on_exit(self, reason, intentional):
        self.ready.wait()
        with self.events_lock:
            was_busy = self.busy()
            self.flush_messages()
            self.closed = True
            self.display_turn_id = None
            self.starting = False
            self.failure = "" if intentional else reason
            if was_busy or not intentional:
                self.uncertain = True
                self.error = reason
                self.inputs.interrupt(reason)
            # Persist changed receipts even when their user messages have paged
            # out of the in-memory history slice, before the generation retires.
            self.persist()
            def stopped(record):
                self.inputs.project(record)
                expire(record, reason)
                record.update(runtime_phase="interrupted" if self.uncertain else "idle", submission_pending=False)
                if self.uncertain:
                    record.update(state="interrupted", reconciliation_required=True, error=reason)
            self.mutate(stopped)
            self.pending.clear()

    def stop(self):
        if self.rpc:
            self.rpc.close()
        self.closed = True
        if self.persistence_failed:
            raise cli.SessionError("invalid_state", "Pi state could not be saved; reservation retained", 409)
        def released(record):
            record["runtime_released_generation"] = self.generation
            if not self.uncertain:
                record.update(reconciliation_required=False, state="error" if self.error else "idle", error=self.error)
        self.mutate(released)

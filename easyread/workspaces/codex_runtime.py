"""One server-hosted Codex runtime, independent of browser connections.

Only explicit user operations start/resume a native thread. Goals are scheduled
by Codex; a turn/completed notification never causes an EasyRead turn/start.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import re
import threading
import time

from . import codex_protocol as protocol, codex_requests as requests, session_cli as cli
from .session_store import expire

RETAIN_GOALS = {"active", "blocked", "usageLimited", "budgetLimited"}
TOOL_TYPES = {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabAgentToolCall"}
MAX_PENDING_BYTES = 1024 * 1024


def state(record):
    pending = [p for p in record.get("pending_requests", []) if not p.get("expired")]
    if any(p.get("is_blocking") for p in pending):
        return "waiting_external" if all(p["kind"] == "elicitation" for p in pending) else "waiting_user"
    if record.get("active_turn_id") or record.get("native_active"):
        return "running"
    if record.get("queued"):
        return "queued"
    goal = (record.get("goal") or {}).get("status")
    if record.get("background_active") and goal not in ("active", "blocked", "usageLimited", "budgetLimited"):
        return "waiting_external"
    return {"active": "running", "paused": "paused", "complete": "completed", "blocked": "waiting_external",
            "usageLimited": "waiting_external", "budgetLimited": "paused"}.get(
                goal, "waiting_external" if record.get("background_active") else "idle")


class Runtime:
    def __init__(self, owner, pid, sid):
        self.owner, self.pid, self.sid = owner, pid, sid
        self.operation = threading.RLock()
        self.lock = threading.RLock()
        self.events_lock = threading.RLock()
        self.message_buffer = {}
        self.message_chunks = {}
        self.last_flush = 0.0
        self.goal_revision = 0
        self.rpc = None
        self.thread_id = ""
        self.generation = ""
        self.active_turn = None
        self.native_active = False
        self.native_status_cursor = 0
        self.goal = None
        self.pending = {}
        self.pending_bytes = 0
        self.tools = set()
        self.children = {}
        self.capabilities = {}
        self.methods = set()
        self.operation_count = 0  # Includes callers waiting for the operation lock.
        self.submission_pending = False
        self.uncertain = False
        self.background = True  # Unknown is not idle; confirm with native list first.
        self.queued = False
        self.queue_revision = 0
        self.cwd = None
        self.last_activity = time.monotonic()
        self.closed = False
        self.starting = True
        self.failure = ""
        self.persistence_failed = False

    def busy(self):
        with self.lock:
            return bool(self.operation_count or self.submission_pending or self.starting or self.uncertain
                        or self.active_turn or self.native_active or self.pending or self.tools
                        or self.background or self.queued or (self.goal or {}).get("status") in RETAIN_GOALS
                        or any(status not in ("completed", "errored", "shutdown", "notFound") for status in self.children.values()))

    def mutate(self, mutate):
        def apply(data):
            record = self.owner.sessions._record(data, self.sid)
            mutate(record)
            record["updated"] = cli.now_iso()
        try:
            self.owner.sessions._commit(self.pid, apply)
        except Exception:
            self.persistence_failed = True
            self.uncertain = True
            raise

    def ensure(self, record):
        if self.rpc:
            if self.closed or self.failure or self.starting:
                raise cli.SessionError("runtime_expired", "Native runtime was interrupted or failed to initialize; cancel before explicitly resuming", 409)
            return
        exe = cli.executable("codex")
        capabilities = protocol.probe(exe)
        if not capabilities.get("app_server"):
            raise cli.SessionError("capability_unavailable", capabilities.get("reason") or "Codex App Server is unsupported", 409)
        self.methods = set(capabilities.pop("methods", []))
        self.capabilities = capabilities
        root, meta = Path(self.owner.sessions.store.root(self.pid)), Path(self.owner.sessions.store.meta(self.pid))
        self.cwd = root
        with self.owner.sessions.store.lock(self.pid):
            env, _, _ = cli.launch_env(meta, "codex", record.get("cli_isolated"))
        argv = [exe, "app-server", "--listen", "stdio://"] + cli.project_mcp_args("codex", Path(self.owner.sessions.store.libroot), self.pid)
        self.thread_id = record.get("native_id") or ""
        if not self.thread_id and (record.get("native_started") or record.get("resumed")):
            raise cli.SessionError("invalid_state", "Native identity is missing; refusing a new conversation", 409)
        self.rpc = protocol.AppServer(argv, root, env, self.on_message, self.on_exit)
        self.generation = self.rpc.generation
        self.mutate(lambda r: r.update(runtime_state="starting", generation=self.generation))
        self.rpc.initialize()
        # Preserve EasyRead's existing explicit trusted/full-native permission policy.
        params = {"cwd": str(root), "approvalPolicy": "never", "sandbox": "danger-full-access"}
        if record.get("model"):
            params["model"] = record["model"]
        if self.thread_id:
            result = self.call("thread/resume", {**params, "threadId": self.thread_id, "excludeTurns": True})
        else:
            result = self.call("thread/start", params)
        thread_id = (result.get("thread") or {}).get("id")
        if not cli.valid_native_id(thread_id) or (self.thread_id and thread_id != self.thread_id):
            self.uncertain = True
            raise cli.SessionError("native_conflict", "Codex returned a different native thread identity", 409)
        self.thread_id = thread_id
        if (result.get("thread", {}).get("status") or {}).get("type") == "active":
            self.native_active = True
        self.mutate(lambda r: r.update(native_id=thread_id, native_started=True, native_active=self.native_active))
        # The installed schema establishes methods; refreshed thread features establish availability.
        features, cursor = {}, None
        if "experimentalFeature/list" in self.methods:
            for _ in range(20):
                page = self.call("experimentalFeature/list", {"threadId": thread_id, "limit": 100, "cursor": cursor})
                features.update({f["name"]: f.get("enabled", False) for f in page.get("data", [])})
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            self.capabilities["goal"] = "thread/goal/set" in self.methods and features.get("goals", False)
            self.capabilities["user_input"] = features.get("default_mode_request_user_input", False)
        if self.capabilities.get("goal"):
            revision = self.goal_revision
            goal = self.call("thread/goal/get", {"threadId": thread_id}).get("goal")
            with self.lock:
                if revision == self.goal_revision:
                    self.goal = goal
        if "thread/queue/list" in self.methods:
            revision = self.queue_revision
            queued = self.call("thread/queue/list", {"threadId": thread_id, "limit": 1})
            with self.lock:
                if revision == self.queue_revision:
                    self.queued = bool(queued.get("data") or queued.get("nextCursor"))
        with self.lock:
            self.starting = False
        def ready(r):
            r.update(native_id=thread_id, native_started=True, runtime_state="awake", goal=self.goal, queued=self.queued,
                     capabilities=self.capabilities, reconciliation_required=False, error="")
            r["state"] = state(r)
        self.mutate(ready)

    def call(self, method, params, **kwargs):
        try:
            return self.rpc.call(method, params, **kwargs)
        except cli.SessionError as error:
            if error.code in ("rpc_uncertain", "runtime_expired") or (isinstance(error, protocol.RpcError) and error.native_code == -32000):
                with self.lock:
                    self.uncertain = True
                self.mutate(lambda r: r.update(state="interrupted", reconciliation_required=True, error=str(error)))
            raise

    def require(self, capability):
        if not self.capabilities.get(capability):
            raise cli.SessionError("capability_unavailable", "This native runtime does not support " + capability, 409)

    def inputs(self, prompt):
        inputs = [{"type": "text", "text": prompt}]
        match = re.match(r"^\s*\$([A-Za-z0-9][A-Za-z0-9_.:-]*)(?=\s|$)", prompt)
        if not match or "skills/list" not in self.methods:
            return inputs
        name = match[1]
        result = self.call("skills/list", {"cwds": [str(self.cwd)], "forceReload": False})
        skills = [skill for entry in result.get("data", []) for skill in entry.get("skills", []) if skill.get("name") == name]
        if not skills:
            return inputs  # Unknown $HOME, prices and examples remain ordinary text.
        if len({skill.get("path") for skill in skills}) != 1:
            raise cli.SessionError("ambiguous_skill", "This skill name has multiple native paths", 409)
        skill = skills[0]
        if not skill.get("enabled") or not isinstance(skill.get("path"), str):
            raise cli.SessionError("skill_unavailable", "This native skill is disabled or unavailable", 409)
        return inputs + [{"type": "skill", "name": name, "path": skill["path"]}]

    def reconcile_status(self):
        """Observe live metadata and fence older queued status notifications."""
        if "thread/read" not in self.methods:
            self.uncertain = True
            raise cli.SessionError("rpc_uncertain", "Native activity cannot be reconciled; cancel before retrying", 409)
        result, cursor = self.call("thread/read", {"threadId": self.thread_id, "includeTurns": False},
                                   notification_cursor=True)
        status = (result.get("thread") or {}).get("status") or {}
        if status.get("type") not in ("active", "idle"):
            self.uncertain = True
            raise cli.SessionError("rpc_uncertain", "Native activity could not be confirmed; cancel before retrying", 409)
        with self.events_lock:
            with self.lock:
                if cursor >= self.native_status_cursor:
                    self.native_status_cursor = cursor
                    self.native_active = status["type"] == "active"
                active = self.native_active
            def observed(r):
                r["native_active"] = active
                r["state"] = state(r)
            self.mutate(observed)

    def resolve_request(self, request_id):
        with self.lock:
            pending = self.pending.pop(protocol.request_key(request_id), None)
            if pending:
                self.pending_bytes -= pending[3]

    def set_goal(self, action, objective=None):
        self.require("goal")
        params = {"threadId": self.thread_id, "origin": "user"}
        revision = self.goal_revision
        if action == "clear":
            self.call("thread/goal/clear", params)
            goal = None
        else:
            if action == "set":
                params.update(objective=objective, status="active")
            else:
                if not self.goal:
                    raise cli.SessionError("no_goal", "This session has no native goal", 409)
                params["status"] = "paused" if action == "pause" else "active"
            goal = self.call("thread/goal/set", params).get("goal")
        with self.lock:
            if self.goal_revision == revision:
                self.goal = goal
            goal = deepcopy(self.goal)
            self.last_activity = time.monotonic()
        if action in ("pause", "clear"):
            # Pausing the Goal scheduler does not imply that its current turn ended.
            self.reconcile_status()
        def update(r):
            r.update(goal=goal, error="", reconciliation_required=False)
            r["state"] = state(r)
            r["events"].append({"kind": "goal", "at": cli.now_iso(), "action": action, "goal": goal})
        self.mutate(update)

    def refresh_background(self):
        if "thread/backgroundTerminals/list" not in self.methods or not self.thread_id or self.closed:
            return  # Cannot prove idle on older protocols; retain until explicit close.
        background, queued = False, False
        revision = self.queue_revision
        for thread_id in [self.thread_id, *list(self.children)]:
            result = self.call("thread/backgroundTerminals/list", {"threadId": thread_id, "limit": 1})
            background = background or bool(result.get("data") or result.get("nextCursor"))
            if "thread/queue/list" in self.methods:
                page = self.call("thread/queue/list", {"threadId": thread_id, "limit": 1})
                queued = queued or bool(page.get("data") or page.get("nextCursor"))
        with self.lock:
            was, was_queued = self.background, self.queued
            self.background = background
            if revision == self.queue_revision:
                self.queued = queued
            queued = self.queued
            effective_background = background or any(s not in ("completed", "errored", "shutdown", "notFound") for s in self.children.values())
            if (was and not background) or (was_queued and not queued):
                self.last_activity = time.monotonic()
        if was != background or was_queued != queued:
            def workload(r):
                r.update(background_active=effective_background, queued=queued)
                if not r.get("error"):
                    r["state"] = state(r)
            self.mutate(workload)

    def flush_messages(self):
        with self.events_lock:
            if not self.message_buffer:
                return
            messages, self.message_buffer = self.message_buffer, {}
            self.last_flush = time.monotonic()
            def flush(record):
                for (item_id, turn_id), text in messages.items():
                    self._message(record, item_id, turn_id, text, delta=True)
            self.mutate(flush)

    def on_message(self, message):
        with self.events_lock:
            self._on_message(message)

    def _on_message(self, message):
        method, params = message.get("method", ""), message.get("params") or {}
        if method == "item/agentMessage/delta" and params.get("threadId") == self.thread_id:
            key = (params.get("itemId"), params.get("turnId"))
            self.message_buffer[key] = self.message_buffer.get(key, "") + (params.get("delta") or "")
            if time.monotonic() - self.last_flush >= .3 or sum(map(len, self.message_buffer.values())) >= 32000:
                self.flush_messages()
            return
        self.flush_messages()
        if method == "thread/started" and not self.thread_id:
            thread_id = (params.get("thread") or {}).get("id") or ""
            if cli.valid_native_id(thread_id):
                self.thread_id = thread_id
                self.mutate(lambda r: r.update(native_id=thread_id, native_started=True))
        if "id" in message:
            try:
                thread_id = params.get("threadId")
                if thread_id != self.thread_id and thread_id not in self.children:
                    raise cli.SessionError("request_scope", "Native request does not belong to this session", 409)
                card = requests.normalize(message, self.generation or self.rpc.generation, thread_id)
            except cli.SessionError as error:
                self.rpc.reply(message["id"], error={"code": -32601, "message": str(error)})
                self.mutate(lambda r: r["events"].append({"kind": "notice", "at": cli.now_iso(), "text": str(error)}))
                return
            size = len(json.dumps([card, params], ensure_ascii=False, separators=(",", ":")).encode())
            with self.lock:
                key = protocol.request_key(message["id"])
                if key in self.pending or len(self.pending) >= 64 or self.pending_bytes + size > MAX_PENDING_BYTES:
                    # Fail the runtime closed; never silently discard a prompt or
                    # manufacture an approval/answer when memory admission fails.
                    raise cli.SessionError("request_capacity", "Native pending request budget exceeded or duplicate request ID", 409)
                self.pending[key] = (card, method, params, size)
                self.pending_bytes += size
                self.last_activity = time.monotonic()
            def pending(r):
                r["pending_requests"] = [p for p in r.get("pending_requests", []) if not p.get("expired")]
                r["pending_requests"].append(card)
                r["state"] = state(r)
            self.mutate(pending)
            return
        if params.get("threadId") != self.thread_id:
            # Child status alone is not proof that its experiment/background tools
            # finished. Retain until native collab status explicitly confirms it.
            if params.get("threadId") in self.children and method == "thread/queue/changed":
                with self.lock:
                    self.queue_revision += 1
                    self.queued = True
                def queued_work(r):
                    r["queued"] = True
                    r["state"] = state(r)
                self.mutate(queued_work)
            if params.get("threadId") in self.children and method == "serverRequest/resolved":
                key = protocol.request_key(params["requestId"])
                self.resolve_request(params["requestId"])
                def resolved(r):
                    r["pending_requests"] = [p for p in r.get("pending_requests", []) if protocol.request_key(p["id"]) != key]
                    r["state"] = state(r)
                self.mutate(resolved)
            return
        with self.lock:
            self.last_activity = time.monotonic()
            if method == "turn/started":
                self.active_turn = (params.get("turn") or {}).get("id")
                self.submission_pending = False
            elif method == "turn/completed":
                if self.active_turn == (params.get("turn") or {}).get("id"):
                    self.active_turn = None
                # Background commands can survive turn completion; query before releasing admission.
                self.background = True
            elif method == "thread/status/changed":
                cursor = message["_notification_cursor"]
                if cursor > self.native_status_cursor:
                    self.native_status_cursor = cursor
                    self.native_active = (params.get("status") or {}).get("type") == "active"
            elif method == "thread/goal/updated":
                self.goal_revision += 1
                self.goal = params.get("goal")
            elif method == "thread/goal/cleared":
                self.goal_revision += 1
                self.goal = None
            elif method == "serverRequest/resolved":
                self.resolve_request(params["requestId"])
            elif method == "thread/queue/changed":
                self.queue_revision += 1
                self.queued = True  # Confirm the native queue before declaring it idle.
            item = params.get("item") or {}
            if item.get("type") == "collabAgentToolCall":
                self.background = True  # Child tools can survive a completed collab call.
                for child in item.get("receiverThreadIds", []):
                    self.children[child] = (item.get("agentsStates", {}).get(child) or {}).get("status", "running")
            if method == "item/started" and item.get("type") in TOOL_TYPES:
                self.tools.add(item.get("id"))
            elif method == "item/completed":
                self.tools.discard(item.get("id"))
            active, goal = self.active_turn, deepcopy(self.goal)
            queued, native_active = self.queued, self.native_active
            background = self.background or any(s not in ("completed", "errored", "shutdown", "notFound") for s in self.children.values())
        def apply(r):
            r.update(active_turn_id=active, goal=goal, runtime_state="awake", queued=queued,
                     background_active=background, native_active=native_active)
            if method == "serverRequest/resolved":
                key = protocol.request_key(params["requestId"])
                r["pending_requests"] = [p for p in r.get("pending_requests", []) if protocol.request_key(p["id"]) != key]
            event = {"at": cli.now_iso(), "kind": "native", "method": method, "turn_id": params.get("turnId")}
            if method in ("turn/started", "turn/completed"):
                turn = params.get("turn") or {}
                if method == "turn/started" and not self.uncertain:
                    r["error"] = ""
                event.update(kind="state", turn_id=turn.get("id"), status=turn.get("status"))
                if turn.get("error"):
                    r.update(error=str(turn["error"].get("message") or "Native turn failed"), state="error")
                    event["error"] = r["error"]
            elif method == "item/agentMessage/delta":
                self._message(r, params.get("itemId"), params.get("turnId"), params.get("delta") or "", delta=True)
                return  # Message projection is itself an incremental journal update.
            elif method in ("item/started", "item/completed"):
                item = params.get("item") or {}
                if item.get("type") == "agentMessage":
                    self._message(r, item.get("id"), params.get("turnId"), item.get("text") or "",
                                  partial=method != "item/completed")
                    return
                if item.get("type") == "userMessage":
                    return  # Submitted user input is already recorded with a stable client ID.
                event.update(kind="tool" if method.endswith("started") else "tool_result", id=item.get("id"),
                             name=item.get("type"), detail=str(item.get("command") or item.get("tool") or ""),
                             text=str(item.get("aggregatedOutput") or item.get("changes") or item.get("status") or ""))
            elif method.endswith("/delta"):
                event.update(kind="thinking" if "reasoning" in method.lower() else "tool_output",
                             text=str(params.get("delta") or ""), id=params.get("itemId"))
            elif method == "thread/tokenUsage/updated":
                usage = (params.get("tokenUsage") or {}).get("total") or {}
                r["usage"] = {"input": usage.get("inputTokens", 0), "output": usage.get("outputTokens", 0),
                              "cached": usage.get("cachedInputTokens", 0)}
                return
            elif method == "thread/status/changed":
                status = params.get("status") or {}
                if status.get("type") == "systemError":
                    r.update(state="error", error="Native thread reported a system error")
            elif method not in ("thread/goal/updated", "thread/goal/cleared", "serverRequest/resolved", "thread/queue/changed", "error"):
                return
            if not r.get("error"):
                r["state"] = state(r)
            text = event.get("text", "")
            if len(text) > 16000:
                r["events"].extend({**event, "text": text[i:i + 16000], "part": i // 16000}
                                   for i in range(0, len(text), 16000))
            else:
                r["events"].append(event)
        self.mutate(apply)

    def _message(self, record, item_id, turn_id, text, *, delta=False, partial=True):
        # Keep each projection bounded without discarding large native answers.
        size = 200000
        index = self.message_chunks.get(item_id, 0) if delta else 0
        remaining = text
        while True:
            mid = "native:" + str(item_id) + ":" + str(index)
            message = next((m for m in record["messages"] if m.get("id") == mid), None)
            if message is None:
                message = self.owner.sessions._journal(self.pid).message(self.sid, mid) or {
                    "id": mid, "role": "assistant", "content": "", "at": cli.now_iso(), "turn_id": turn_id}
                record["messages"].append(message)
            prefix = message["content"] if delta else ""
            taken = max(0, size - len(prefix))
            message.update(content=prefix + remaining[:taken], partial=partial)
            remaining = remaining[taken:]
            if not remaining:
                break
            index += 1
        if partial:
            self.message_chunks[item_id] = index
        else:
            self.message_chunks.pop(item_id, None)

    def on_exit(self, reason, intentional):
        self.flush_messages()
        with self.lock:
            was_busy = self.busy()
            self.closed = True
            self.starting = False
            self.failure = "" if intentional else reason
        if intentional and not was_busy:
            reason = "Idle native runtime released; context remains on disk"
        def stopped(r):
            expire(r, reason)
            if was_busy or not intentional:
                r.update(state="interrupted", reconciliation_required=True, error=reason)
            r["events"].append({"kind": "runtime", "at": cli.now_iso(), "state": "sleeping", "text": reason})
        self.mutate(stopped)

    def stop(self):
        if self.rpc:
            self.rpc.close()
        self.closed = True
        if self.persistence_failed:
            raise cli.SessionError("invalid_state", "Native session state could not be saved; reservation retained", 409)

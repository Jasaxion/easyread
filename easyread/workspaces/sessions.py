"""Durable research sessions and the stable HTTP/archive compatibility seam.

``session_store`` owns indexed journals (legacy state.json remains readable),
``codex_sessions`` owns server-hosted App Server runtimes and native Goals;
``session_controls`` owns idle settings and exclusive native TUI admission.
The ordinary Claude exec worker and native transcript synchronization stay here.
All adapters retain project-private native homes, identities, and archive state.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

from ..app.i18n import tr
from ..app.log import log
from . import session_cli as cli
from . import session_terminal as term
from . import session_controls as controls, session_reasoning as reasoning
from .session_cli import SessionError

SID = re.compile(r"^[a-z0-9]{6,32}$")
TITLE_MAX = 120
MESSAGE_MAX = 200_000
KEEP_MESSAGES = 40
KEEP_EVENTS = 200
MAX_SESSIONS = 60
MANIFEST = "manifest.json"
ARCHIVE_KIND = "easyread-workspace-sessions"
FLUSH_INTERVAL = .3


def turn_timeout() -> int:
    """Optional deployment limit for Claude exec only; long work is unlimited by default."""
    try:
        return max(0, int(os.environ.get("EASYREAD_WORKSPACE_TURN_TIMEOUT") or 0))
    except ValueError:
        return 0


class _Job:
    def __init__(self, pid: str, sid: str, turn_id: str | None = None):
        self.pid, self.sid = pid, sid
        self.turn_id = turn_id or secrets.token_hex(8)
        self.proc: subprocess.Popen | None = None
        self.cancelled = False
        self.finalization_error = False
        self.thread: threading.Thread | None = None


class Sessions:
    """Stable seam for the HTTP adapter: one instance per store."""

    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()
        self._jobs: dict[tuple[str, str], _Job] = {}
        self._operations = {}
        self._recovered: set[str] = set()
        self._closed = False
        from .codex_sessions import CodexSessions
        self.native = CodexSessions(self)
        # Eager, so no lock is taken while a project lock is held.
        self.terminals = term.NativeTerminals(_NullAccess())

    # ---------- paths and persistence ----------

    def _dir(self, pid: str) -> Path:
        path = Path(self.store.meta(pid)) / "sessions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _file(self, pid: str) -> Path:
        return self._dir(pid) / "state.json"

    def _legacy_load(self, pid: str) -> dict:
        path = self._file(pid)
        try:
            from .files import read_bytes
            data = json.loads(read_bytes(path).decode("utf-8")) if path.exists() or path.is_symlink() else {
                "version": 1, "sessions": {}, "order": [], "seq": 0}
            if (not isinstance(data, dict) or data.get("version") != 1
                    or not isinstance(data.get("sessions"), dict)
                    or not isinstance(data.get("order"), list)
                    or type(data.get("seq")) is not int):
                raise ValueError()
            for sid, record in data["sessions"].items():
                if (not SID.fullmatch(sid) or not isinstance(record, dict) or record.get("id") != sid
                        or record.get("engine") not in cli.ENGINES
                        or not isinstance(record.get("messages"), list)
                        or not isinstance(record.get("events"), list)
                        or any(not isinstance(m, dict) or m.get("role") not in ("user", "assistant")
                               or not isinstance(m.get("content"), str) for m in record["messages"])
                        or any(not isinstance(e, dict) for e in record["events"])
                        or (record.get("native_id") and not cli.valid_native_id(record["native_id"]))):
                    raise ValueError()
            if (any(not isinstance(sid, str) or sid not in data["sessions"] for sid in data["order"])
                    or len(set(data["order"])) != len(data["order"])):
                raise ValueError()
        except (OSError, ValueError, TypeError):
            raise SessionError("invalid_state", "Session metadata is unreadable; existing history was not changed", 409) from None
        data["order"] += [sid for sid in data["sessions"] if sid not in data["order"]]
        return data

    def _journal(self, pid):
        from .session_store import Journal
        return Journal(self._dir(pid))

    def _load(self, pid: str, full=False) -> dict:
        from .session_store import expire
        journal = self._journal(pid)
        journal.initialize(lambda: self._legacy_load(pid))
        data = journal.load(full=full)
        changed = False
        # Project lock precedes manager lock; never hold either during native RPC.
        with self._lock:
            self._prune_jobs()
            if pid not in self._recovered:
                for summary in list(data["sessions"].summaries()):
                    sid = summary["id"]
                    if (summary.get("state") in ("running", "queued", "waiting_user", "waiting_external")
                            or summary.get("pending_requests") or summary.get("runtime_state") in ("awake", "starting")
                            or (summary.get("goal") or {}).get("status") in ("active", "blocked", "usageLimited", "budgetLimited")):
                        if (pid, sid) not in self._jobs:
                            record = data["sessions"][sid]
                            expire(record, "Service restarted; reconcile external work before explicitly resuming")
                            changed = True
            changed = self._sync_terminals(pid, data) or changed
            if changed:
                self._write(pid, data)
            self._recovered.add(pid)
        return data

    def _prune_jobs(self) -> None:
        for key, job in list(self._jobs.items()):
            if (job.thread is not None and not job.thread.is_alive() and not job.finalization_error
                    and (job.proc is None or job.proc.poll() is not None)):
                self._jobs.pop(key)

    def _native_home(self, pid: str, record: dict) -> Path:
        return cli.state_home(Path(self.store.meta(pid)), record["engine"], record.get("cli_isolated"))

    def _sync_terminals(self, pid: str, data: dict) -> bool:
        changed = False
        root = Path(self.store.root(pid))
        for summary in list(data["sessions"].summaries()):
            sid = summary["id"]
            tracking = summary.get("terminal_tracking")
            if not isinstance(tracking, dict):
                continue
            record = data["sessions"][sid]
            engine, native_id = record["engine"], record.get("native_id") or ""
            cli_home = self._native_home(pid, record)
            active = self.terminals.scope_active(pid, sid)
            if not native_id:
                candidates = cli.project_transcripts(engine, cli_home, root)
                new_ids = set(candidates) - set(tracking.get("baseline", []))
                if len(new_ids) > 1:
                    raise SessionError("native_conflict", "Cannot identify native terminal session unambiguously", 409)
                if new_ids:
                    native_id = new_ids.pop()
                    if any(r is not record and r.get("engine") == engine and r.get("native_id") == native_id
                           for r in data["sessions"].values()):
                        raise SessionError("native_conflict", "Native session already belongs to another conversation", 409)
                    record["native_id"] = native_id
                    changed = True
            if native_id:
                # Do not parse a half-written final line while a TUI is streaming.
                paths = cli.project_transcripts(engine, cli_home, root)
                path = paths.get(native_id)
                if path:
                    if not record.get("native_started"):
                        record["native_started"] = True
                        changed = True
                    messages = cli.transcript_messages(engine, cli.read_transcript(path))
                    count = tracking.get("message_count", 0)
                    if len(messages) > count:
                        record["messages"].extend(messages[count:])
                        record["events"].append({"at": cli.now_iso(), "kind": "terminal_sync",
                                                 "messages": len(messages) - count})
                        tracking["message_count"] = len(messages)
                        record["updated"] = cli.now_iso()
                        changed = True
            if not active:
                record.pop("terminal_tracking", None)
                changed = True
        return changed

    def _write(self, pid: str, data: dict) -> None:
        self._journal(pid).save(data)

    def _commit(self, pid: str, mutate):
        with self.store.lock(pid):
            data = self._load(pid)
            result = mutate(data)
            self._write(pid, data)
            return result

    def _record(self, data: dict, sid: str) -> dict:
        record = data["sessions"].get(sid)
        if not isinstance(record, dict):
            raise SessionError("not_found", tr("会话不存在"), 404)
        return record

    # ---------- public API ----------

    def capabilities(self) -> dict:
        from .codex_protocol import probe
        codex = {k: v for k, v in probe().items() if k != "methods"}
        return {"sessions": True, "engines": {"codex": codex,
                    "claude": {"goal": False, "steer": False, "user_input": False, "adapter": "claude_exec"}}, "terminal": term.enabled(),
                "terminal_reason": term.unavailable_reason(), "mcp": True,
                "cli_isolation": cli.isolation_enabled()}

    def listing(self, pid: str) -> dict:
        self.store.get(pid)
        terminal = term.enabled()
        with self.store.lock(pid):
            data = self._load(pid)
            summaries = list(data["sessions"].summaries())
            sessions = [self._summary(pid, r) for r in summaries]
        capabilities = self.capabilities()
        engines = [cli.engine_status(e, terminal) for e in cli.ENGINES]
        for engine in engines:
            engine["capabilities"].update(capabilities["engines"][engine["id"]])
        return {"sessions": sessions, "engines": engines, "capabilities": capabilities, "runtime": self.native.status()}

    def get(self, pid: str, sid: str) -> dict:
        self.store.get(pid)
        self._check_sid(sid)
        with self.store.lock(pid):
            data = self._load(pid)
            record = next((r for r in data["sessions"].summaries() if r["id"] == sid), None)
            if record is None:
                raise SessionError("not_found", "Session not found", 404)
            page = self._journal(pid).page(sid)
            return {"session": self._public(pid, {**record, **page}), "cursor": record.get("cursor", 0),
                    "history_before": page["history_before"], "has_more": page["has_more"]}

    def updates(self, pid, sid, after=0, limit=100):
        self.store.get(pid)
        self._check_sid(sid)
        with self.store.lock(pid):
            data = self._load(pid)
            record = next((r for r in data["sessions"].summaries() if r["id"] == sid), None)
            if record is None:
                raise SessionError("not_found", "Session not found", 404)
            return {"session": self._summary(pid, record), "pending_requests": record.get("pending_requests", []),
                    **self._journal(pid).updates(sid, after, limit)}

    def history(self, pid, sid, before=None, limit=40):
        self.store.get(pid)
        self._check_sid(sid)
        with self.store.lock(pid):
            data = self._load(pid)
            if sid not in data["sessions"]:
                raise SessionError("not_found", "Session not found", 404)
            return self._journal(pid).page(sid, before, limit)

    def create(self, pid: str, body: dict | None = None) -> dict:
        project = self.store.get(pid)
        body = body if isinstance(body, dict) else {}
        engine = self._engine(body.get("engine") or (project or {}).get("engine") or "codex")
        title = self._title(body.get("title"), required=False)
        model = self._model(body.get("model"))
        effort = reasoning.validate(self.store, pid, engine, model, body.get("reasoning_effort", ""))
        requested = body.get("native_id") or ""
        def mutate(data):
            if requested:
                for record in data["sessions"].values():
                    if record.get("engine") == engine and record.get("native_id") == requested:
                        return record
            native_id = self._verify_native(pid, engine, requested) if requested else (
                cli.new_native_id(engine) if engine == "claude" else "")
            if len(data["sessions"]) >= MAX_SESSIONS:
                raise SessionError("too_many", tr("会话数量已达上限，请先删除旧会话"), 409)
            sid = self._new_sid(data)
            now = cli.now_iso()
            record = {"id": sid, "engine": engine, "model": model, "native_id": native_id,
                      "reasoning_effort": effort,
                      "title": title or tr("会话 {n}", n=len(data["sessions"]) + 1),
                      "state": "idle", "created": now, "updated": now, "messages": [], "events": [],
                      "error": "", "usage": {}, "resumed": bool(requested),
                      "cli_isolated": cli.isolation_enabled()}
            if requested:
                path = cli.transcript(engine, self._native_home(pid, record), native_id, self.store.root(pid))
                record["messages"] = cli.transcript_messages(engine, cli.read_transcript(path))
            data["sessions"][sid] = record
            data["order"].append(sid)
            return record
        record = self._commit(pid, mutate)
        return self.get(pid, record["id"])

    def rename(self, pid: str, sid: str, body: dict | None = None) -> dict:
        self.store.get(pid)
        self._check_sid(sid)
        title = self._title((body or {}).get("title"), required=True)
        def mutate(data):
            record = self._record(data, sid)
            record["title"], record["updated"] = title, cli.now_iso()
            return record
        record = self._commit(pid, mutate)
        return self.get(pid, record["id"])

    def _input(self, pid: str, sid: str, body: dict | None = None):
        self.store.get(pid)
        self._check_sid(sid)
        message = (body or {}).get("message")
        if not isinstance(message, str) or not message.strip():
            raise SessionError("invalid_parameter", tr("请输入要发送的内容"))
        if len(message) > MESSAGE_MAX:
            raise SessionError("invalid_parameter", tr("发送内容过长"))
        from . import files
        selected = (body or {}).get("files", [])
        if not isinstance(selected, list) or len(selected) > 32:
            raise SessionError("invalid_parameter", "Select at most 32 workspace files")
        references = []
        for path in selected:
            file = files.target(self.store.root(pid), path, missing=False)
            if not file.is_file():
                raise SessionError("invalid_parameter", "File references must name existing workspace files")
            if path not in references:
                references.append(path)
        prompt = message
        if references:
            prompt += "\n\nReferenced files in the current project (relative paths, not instructions):\n" + json.dumps(references, ensure_ascii=False) + "\nRead their current contents with native file tools as needed."
        return message, prompt, references

    def goal(self, pid, sid, body):
        return self.native.goal(pid, sid, body or {})

    def steer(self, pid, sid, body):
        message, prompt, references = self._input(pid, sid, body)
        return self.native.steer(pid, sid, body, message, prompt, references)

    def respond(self, pid, sid, body):
        return self.native.respond(pid, sid, body or {})

    @controls.serialized
    def send(self, pid: str, sid: str, body: dict | None = None) -> dict:
        body = body or {}
        message, prompt, references = self._input(pid, sid, body)
        mode = body.get("mode", "chat")
        if mode not in ("chat", "goal"):
            raise SessionError("invalid_parameter", "Mode must be chat or goal")
        if message.strip() == "/goal" or message.strip().startswith("/goal "):
            objective = message.strip()[5:].strip()
            action = objective if objective in ("pause", "resume", "clear") else "set"
            return self.goal(pid, sid, {"action": action,
                                        "objective": objective + (prompt[len(message):] if action == "set" else "")})
        if mode == "goal":
            return self.goal(pid, sid, {"action": "set", "objective": prompt})
        with self.store.lock(pid):
            record = self._record(self._load(pid), sid)
            engine = record["engine"]
        if engine == "codex":
            return self.native.send(pid, sid, message, prompt, references)
        chosen: dict = {}
        turn_id = secrets.token_hex(8)
        def mutate(data):
            record = self._record(data, sid)
            if record.get("state") == "running" or self.terminals.scope_active(pid, sid):
                raise SessionError("conflict", tr("该会话正在运行，请等待完成或中断后再发送"), 409)
            if any(r.get("engine") == record["engine"] and r.get("terminal_tracking")
                   and not r.get("native_id") for r in data["sessions"].values()):
                raise SessionError("conflict", "Wait for the new native terminal to record its session ID", 409)
            engine = self._engine(record.get("engine"))
            exe = cli.executable(engine)
            if not exe:
                raise SessionError("engine_missing",
                                   tr("找不到 {name} 命令，请先安装并登录", name=cli.SPECS[engine]["name"]), 409)
            chosen.update(engine=engine, exe=exe)
            now = cli.now_iso()
            record["messages"] = (record.get("messages") or []) + [
                {"role": "user", "content": message, "at": now, "turn_id": turn_id, **({"files": references} if references else {})}]
            record.update(state="running", error="", updated=now, started=now)
            record["events"] = (record.get("events") or []) + [
                {"at": now, "kind": "queued", "turn_id": turn_id, "text": tr("已提交给 {name}", name=cli.SPECS[engine]["name"])}]
            return record
        with self.store.lock(pid), self._lock:
            self._prune_jobs()
            if self._closed:
                raise SessionError("unavailable", tr("服务正在关闭"), 409)
            if (pid, sid) in self._jobs:
                raise SessionError("conflict", tr("该会话正在运行，请等待完成或中断后再发送"), 409)
            if len(self._jobs) + self.native.count_active() >= self.native.max_active:
                raise SessionError("active_capacity", "Active session capacity reached", 429)
            record = self._commit(pid, mutate)
            job = _Job(pid, sid, turn_id)
            self._jobs[(pid, sid)] = job
            job.thread = threading.Thread(target=self._run, args=(job, dict(record), prompt, chosen["exe"]),
                                          name=f"easyread-session-{sid}", daemon=True)
            job.thread.start()
        return self.get(pid, sid)

    @controls.serialized
    def cancel(self, pid: str, sid: str, body: dict | None = None) -> dict:
        self.store.get(pid)
        self._check_sid(sid)
        self.native.stop(pid, sid, cancel=True)
        with self.store.lock(pid), self._lock:
            self._record(self._load(pid), sid)
            job = self._jobs.get((pid, sid))
            if job:
                job.cancelled = True
        self.terminals.close_scope(pid, sid)
        # The worker needs the project lock to finish. Never join under that lock.
        if job:
            self._kill(job)
            if job.thread:
                job.thread.join(timeout=10)
            if job.thread and job.thread.is_alive():
                raise SessionError("conflict", "Session is still stopping; retry after it exits", 409)
            if job.finalization_error:
                raise SessionError("invalid_state", "Final session state could not be saved", 409)
        return self.get(pid, sid)

    @controls.serialized
    def delete(self, pid: str, sid: str, body: dict | None = None) -> dict:
        self.cancel(pid, sid)
        with self.store.lock(pid), self._lock:
            self._prune_jobs()
            if (pid, sid) in self._jobs or self.native.loaded(pid, sid) or self.terminals.scope_active(pid, sid):
                raise SessionError("conflict", "Session is still in use", 409)
            data = self._load(pid)
            record = self._record(data, sid)
            self._drop_native(pid, record)
            del data["sessions"][sid]
            data["order"].remove(sid)
            self._write(pid, data)
        return {"ok": True}

    def busy(self, pid: str | None = None) -> bool:
        with self._lock:
            self._prune_jobs()
            if any(pid is None or key[0] == pid for key in (*self._jobs, *self._operations)):
                return True
        return self.native.busy(pid) or self.terminals.scope_active(pid)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
        native_error = None
        try:
            self.native.close()
        except Exception as error:
            native_error = error
        for job in jobs:
            job.cancelled = True
            self._kill(job)
        for job in jobs:
            if job.thread:
                job.thread.join(timeout=5)
        self.terminals.close()
        if native_error:
            raise native_error
        if any((job.thread and job.thread.is_alive()) or job.finalization_error
               or (job.proc and job.proc.poll() is None) for job in jobs):
            raise SessionError("conflict", "Native sessions are still stopping", 409)

    # ---------- native terminal ----------

    def attach_access(self, access) -> None:
        """Bind the deployment's access gate (call once from the HTTP adapter)."""
        self.terminals.access = access

    def operation(self, pid, sid):
        return controls.operation(self, pid, sid)

    def settings(self, pid, sid, body):
        return controls.settings(self, pid, sid, body)

    def terminal(self, pid: str, sid: str, access=None, access_sid: str | None = None) -> dict:
        return controls.terminal(self, pid, sid, access, access_sid)

    def relay(self, handler, access_sid: str) -> None:
        self.terminals.relay(handler, access_sid)

    # ---------- export / import ----------

    def prepare_export(self, pid):
        if self.busy(pid):
            raise SessionError("conflict", "Stop native work before exporting", 409)
        self.native.sleep_project(pid)

    def require_sleeping(self, pid):
        if self.native.loaded(pid):
            raise SessionError("conflict", "Native runtime is still loaded; retry after idle reclamation", 409)

    def export_state(self, pid: str, dest) -> dict:
        # Some archive callers already hold a project lock: never stop/join RPC here.
        self.require_sleeping(pid)
        from . import session_archive
        return session_archive.export_state(self, pid, dest)

    def import_state(self, pid: str, source) -> dict:
        """Read full native v2 bundles or legacy transcript-only v1 archives."""
        self.store.get(pid)
        from .files import read_bytes
        base = Path(source).absolute()
        try:
            if not (base / MANIFEST).exists():
                return {"sessions": 0, "native": 0}
            manifest = json.loads(read_bytes(base / MANIFEST).decode("utf-8"))
        except (OSError, ValueError):
            raise SessionError("invalid_archive", tr("会话归档内容无效")) from None
        if isinstance(manifest, dict) and type(manifest.get("version")) is int and manifest["version"] == 2:
            from . import session_archive
            return session_archive.import_state(self, pid, base, manifest)
        if (not isinstance(manifest, dict) or manifest.get("kind") != ARCHIVE_KIND
                or type(manifest.get("version")) is not int or manifest["version"] != 1):
            raise SessionError("invalid_archive", tr("会话归档内容无效"))
        items = manifest.get("sessions")
        if not isinstance(items, list) or len(items) > MAX_SESSIONS:
            raise SessionError("invalid_archive", tr("会话归档内容无效"))
        root, meta = Path(self.store.root(pid)), Path(self.store.meta(pid))
        adopted, transcripts, seen = [], [], set()
        for item in items:
            if (not isinstance(item, dict) or item.get("engine") not in cli.ENGINES
                    or not isinstance(item.get("id"), str) or not SID.fullmatch(item["id"])
                    or item["id"] in seen):
                raise SessionError("invalid_archive", tr("会话归档内容无效"))
            seen.add(item["id"])
            engine = item["engine"]
            messages, events = item.get("messages", []), item.get("events", [])
            if (not isinstance(messages, list) or not isinstance(events, list)
                    or any(not isinstance(m, dict) or m.get("role") not in ("user", "assistant")
                           or not isinstance(m.get("content"), str) for m in messages)
                    or any(not isinstance(e, dict) for e in events)
                    or (item.get("native_id") and not cli.valid_native_id(item["native_id"]))):
                raise SessionError("invalid_archive", tr("会话归档内容无效"))
            text = self._archive_transcript(base, engine, item.get("native"))
            native_id = cli.new_native_id(engine) if text or engine == "claude" else ""
            if text:
                cli.validate_transcript(engine, text, item.get("native_id") or None)
                transcripts.append((cli.transcript_target(engine, cli.project_home(meta, engine), native_id, root),
                                    cli.relocate(engine, text, native_id, root)))
            now = cli.now_iso()
            adopted.append({"engine": engine, "model": self._model(item.get("model")),
                            "reasoning_effort": reasoning.effort(item.get("reasoning_effort", ""), engine),
                            "title": self._title(item.get("title"), required=False) or tr("导入的会话"),
                            "native_id": native_id, "state": "idle", "created": item.get("created") or now,
                            "updated": now, "messages": messages, "events": events,
                            "error": "", "usage": item.get("usage") or {}, "imported": True,
                            "cli_isolated": True, "native_started": bool(text), "native_complete": False})
        written = []
        with self.store.lock(pid):
            data = self._load(pid)
            if len(data["sessions"]) + len(adopted) > MAX_SESSIONS:
                raise SessionError("too_many", tr("会话数量已达上限，请先删除旧会话"), 409)
            try:
                for path, text in transcripts:
                    cli.write_transcript(path, text)
                    written.append(path)
                for record in adopted:
                    sid = self._new_sid(data)
                    data["sessions"][sid] = {**record, "id": sid}
                    data["order"].append(sid)
                self._write(pid, data)
            except Exception:
                for path in written:
                    path.unlink(missing_ok=True)
                raise
        return {"sessions": len(adopted), "native": len(transcripts), "native_complete": False}

    def _archive_transcript(self, base: Path, engine: str, name) -> str:
        if not name:
            return ""
        if not isinstance(name, str) or not re.fullmatch(rf"native/{engine}/[a-z0-9]{{6,32}}\.jsonl", name):
            raise SessionError("invalid_archive", tr("会话归档内容无效"))
        return cli.read_transcript(base / name)

    def _drop_native(self, pid: str, record: dict) -> None:
        engine, native_id = record.get("engine"), record.get("native_id") or ""
        if engine not in cli.ENGINES or not native_id or not record.get("cli_isolated", cli.isolation_enabled()):
            return
        try:
            path = cli.transcript(engine, cli.project_home(Path(self.store.meta(pid)), engine),
                                  native_id, Path(self.store.root(pid)))
            if path:
                path.unlink(missing_ok=True)
        except OSError:
            pass

    # ---------- background turn ----------

    def _run(self, job: _Job, record: dict, message: str, exe: str) -> None:
        engine, pid, sid = record["engine"], job.pid, job.sid
        stream = cli.Stream(engine)
        pending: list[dict] = []
        stderr: list[str] = []
        last_message = None
        timer = None
        code = -1
        try:
            root, meta = Path(self.store.root(pid)), Path(self.store.meta(pid))
            env, cli_home, _ = cli.launch_env(meta, engine, record.get("cli_isolated"))
            native_id = record.get("native_id") or ""
            resume = bool(native_id) and bool(cli.transcript(engine, cli_home, native_id, root))
            if not resume and (record.get("resumed") or record.get("native_started")
                               or any(m["role"] == "assistant" for m in record["messages"])):
                raise SessionError("not_found", "Native transcript is missing; refusing to start an unrelated conversation", 409)
            if engine == "codex":
                tmp = self._dir(pid) / "tmp"
                tmp.mkdir(parents=True, exist_ok=True)
                last_message = tmp / f"{sid}.txt"
                last_message.write_text("", encoding="utf-8")
            argv = cli.argv(engine, exe=exe, prompt_stdin=True, native_id=native_id or None, resume=resume,
                            model=record.get("model") or "", libroot=Path(self.store.libroot), pid=pid,
                            root=root, last_message=last_message, reasoning_effort=record.get("reasoning_effort", ""))
            pending.append({"at": cli.now_iso(), "kind": "notice", "subtype": "launch",
                            "text": (f"resume {native_id}" if resume else tr("新建原生会话"))})
            proc = subprocess.Popen(argv, cwd=str(root), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                    start_new_session=os.name != "nt",
                                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0)
            with self._lock:
                job.proc = proc
            if job.cancelled:
                self._kill(job)
            drain = threading.Thread(target=_drain, args=(proc.stderr, stderr), daemon=True)
            drain.start()
            timeout = turn_timeout()
            if timeout:
                timer = threading.Timer(timeout, self._expire, args=(job,))
                timer.daemon = True
                timer.start()
            try:
                proc.stdin.write(message)
                proc.stdin.close()
            except (OSError, ValueError):
                pass
            last_flush = time.monotonic()
            for line in proc.stdout:
                pending.extend(stream.feed(line))
                if pending and time.monotonic() - last_flush >= FLUSH_INTERVAL:
                    self._flush(pid, sid, pending, stream, job.turn_id)
                    pending, last_flush = [], time.monotonic()
            code = proc.wait()
            drain.join(timeout=2)
        except Exception as error:  # Worker failures must remain visible and release resources.
            log.warning("研究项目会话启动失败：%s", error)
            stderr.append(str(error))
        finally:
            if timer is not None:
                timer.cancel()
            if job.proc is not None and job.proc.poll() is None:
                self._kill(job)
        text = stream.text
        if not text and last_message is not None:
            try:
                text = last_message.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                text = ""
        try:
            self._finish(job, stream, pending, text, code, stderr)
        except (OSError, ValueError):
            job.finalization_error = True
        if last_message is not None:
            last_message.unlink(missing_ok=True)

    def _flush(self, pid: str, sid: str, events: list[dict], stream: cli.Stream, turn_id: str | None = None) -> None:
        if turn_id:
            events = [{**event, "turn_id": turn_id} for event in events]
        def mutate(data):
            record = data["sessions"].get(sid)
            if not isinstance(record, dict):
                return None
            record["events"] = (record.get("events") or []) + events
            record["updated"] = cli.now_iso()
            if stream.native_id and record.get("native_id") != stream.native_id:
                record["native_id"] = stream.native_id
            if stream.native_id:
                record["native_started"] = True
            if stream.text:
                _partial(record, stream.text, turn_id=turn_id)
            return None
        try:
            self._commit(pid, mutate)
        except (OSError, ValueError) as error:
            log.warning("研究项目会话状态写入失败：%s", error)

    def _finish(self, job: _Job, stream: cli.Stream, pending: list[dict], text: str,
                code: int, stderr: list[str]) -> None:
        tail = "".join(stderr).strip()[-600:]
        if job.cancelled:
            state, error = "interrupted", ""
        elif stream.error:
            state, error = "error", stream.error
        elif code != 0 or not stream.finished:
            notice = next((n for n in reversed(stream.notices) if n), "")
            state = "error"
            error = notice or tail or tr("原生 CLI 异常退出（代码 {code}）", code=code)
        elif not text:
            state, error = "error", tr("原生 CLI 未返回结果")
        else:
            state, error = "idle", ""
        events = [{**event, "turn_id": job.turn_id} for event in pending] + [
            {"at": cli.now_iso(), "kind": "state", "state": state, "error": error,
             "exit_code": code, "turn_id": job.turn_id}]
        def mutate(data):
            record = data["sessions"].get(job.sid)
            if not isinstance(record, dict):
                return None
            record["events"] = (record.get("events") or []) + events
            if stream.native_id and record.get("native_id") != stream.native_id:
                record["native_id"] = stream.native_id
            if stream.native_id:
                record["native_started"] = True
            if text:
                _partial(record, text, partial=state != "idle", turn_id=job.turn_id)
            record.update(state=state, error=error, updated=cli.now_iso())
            if stream.usage:
                record["usage"] = stream.usage
            if stream.model:
                record["reported_model"] = stream.model
            return None
        try:
            self._commit(job.pid, mutate)
        except (OSError, ValueError) as error:
            log.warning("研究项目会话状态写入失败：%s", error)
            raise

    def _expire(self, job: _Job) -> None:
        with self._lock:
            if self._jobs.get((job.pid, job.sid)) is not job:
                return
        log.warning("研究项目会话超时，已终止：%s/%s", job.pid, job.sid)
        self._kill(job)

    def _kill(self, job: _Job) -> None:
        with self._lock:
            proc = job.proc
        if proc is None:
            return
        cli.stop_process(proc)

    # ---------- helpers ----------

    def _summary(self, pid: str, record: dict) -> dict:
        out = {k: v for k, v in record.items() if k not in
               ("messages", "events", "pending_requests", "terminal_tracking", "history_before", "has_more")}
        out["adapter"] = "codex_app_server" if record["engine"] == "codex" else "claude_exec"
        out["reasoning_effort"] = record.get("reasoning_effort", "")
        out["goal"] = record.get("goal")
        out["pending_count"] = sum(not r.get("expired") for r in record.get("pending_requests", []))
        out["runtime_state"] = record.get("runtime_state", "sleeping")
        out["terminal_open"] = self.terminals.scope_active(pid, record.get("id"))
        out["resumable"] = bool(record.get("native_id") and (record.get("native_started") or record.get("resumed")))
        return out

    def _public(self, pid: str, record: dict) -> dict:
        out = self._summary(pid, record)
        out.update(events=(record.get("events") or [])[-KEEP_EVENTS:],
                   messages=(record.get("messages") or [])[-KEEP_MESSAGES:],
                   pending_requests=record.get("pending_requests", []))
        return out

    def _new_sid(self, data: dict) -> str:
        for _ in range(50):
            sid = secrets.token_hex(6)
            if sid not in data["sessions"]:
                return sid
        raise SessionError("conflict", tr("无法创建会话，请重试"), 409)

    def _check_sid(self, sid) -> str:
        if not isinstance(sid, str) or not SID.match(sid):
            raise SessionError("not_found", tr("会话不存在"), 404)
        return sid

    def _engine(self, value) -> str:
        if value not in cli.ENGINES:
            raise SessionError("invalid_parameter", tr("请选择 Codex 或 Claude Code"))
        return value

    def _title(self, value, required: bool) -> str:
        if value is None or value == "":
            if required:
                raise SessionError("invalid_parameter", tr("请输入会话名称"))
            return ""
        if not isinstance(value, str) or len(value) > TITLE_MAX or any(ord(c) < 32 for c in value):
            raise SessionError("invalid_parameter", tr("会话名称无效"))
        title = value.strip()
        if required and not title:
            raise SessionError("invalid_parameter", tr("请输入会话名称"))
        return title

    @staticmethod
    def _model(value) -> str:
        if value in (None, ""):
            return ""
        if (not isinstance(value, str) or len(value) > 120
                or not re.fullmatch(r"[A-Za-z0-9 ._:+/-]+(?:\[1m\])?", value)):
            raise SessionError("invalid_parameter", tr("模型名称无效"))
        return value

    def _verify_native(self, pid: str, engine: str, native_id) -> str:
        """A resume id must already belong to this project, never another session."""
        if not cli.valid_native_id(native_id):
            raise SessionError("invalid_parameter", tr("原生会话 ID 无效"))
        cli_home = cli.state_home(Path(self.store.meta(pid)), engine)
        if not cli.transcript(engine, cli_home, native_id, Path(self.store.root(pid))):
            raise SessionError("not_found", tr("该原生会话不属于当前研究项目"), 404)
        return native_id


def _partial(record: dict, text: str, partial: bool = True, turn_id: str | None = None) -> None:
    messages = record.get("messages") or []
    entry = {"role": "assistant", "content": text, "at": cli.now_iso()}
    if turn_id:
        entry["turn_id"] = turn_id
    if partial:
        entry["partial"] = True
    if (messages and messages[-1].get("role") == "assistant" and messages[-1].get("partial")
            and (not turn_id or messages[-1].get("turn_id") == turn_id)):
        entry["id"] = messages[-1].get("id") or secrets.token_hex(12)
        if "seq" in messages[-1]:
            entry["seq"] = messages[-1]["seq"]
        messages[-1] = entry
    else:
        messages = messages + [entry]
    record["messages"] = messages


def _drain(stream, sink: list[str], limit: int = 64 * 1024) -> None:
    total = 0
    try:
        for line in stream:
            if total < limit:
                sink.append(line)
                total += len(line)
    except (OSError, ValueError):
        pass


class _NullAccess:
    """Used until the HTTP adapter binds the real access gate."""

    enabled = False

    def valid(self, sid) -> bool:
        return True

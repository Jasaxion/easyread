"""Bounded, durable browser imports. Network work never holds the submit request."""
from __future__ import annotations

from collections import deque
from concurrent.futures import CancelledError
from hashlib import sha256
import json
import re
import threading
import time
from uuid import uuid4

from ..app.i18n import tr
from ..app.log import log
from ..library.store import read_json, write_json_atomic
from ..research import tasks
from ..translate import instructions, langs
from .import_flow import finish_import
from . import pwa

_ACTIVE = {"queued", "running", "cancelling"}
_INIT = threading.Lock()


class ImportTaskError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def request_options(body):
    if not isinstance(body, dict):
        raise ImportTaskError(tr("导入参数必须是对象"))
    ref = body.get("ref", "")
    if not isinstance(ref, str) or not ref.strip() or len(ref) > 4096:
        raise ImportTaskError(tr("请输入有效的链接、编号或论文标题"))
    result = {"ref": ref.strip().strip("<>"), "kind": body.get("kind", "auto")}
    if result["kind"] not in ("auto", "web", "pdf"):
        raise ImportTaskError(tr("链接类型不正确"))
    for name, default in (("translate", True), ("read", False), ("pdf_export", False)):
        value = body.get(name, default)
        if type(value) is not bool:
            raise ImportTaskError(tr("导入选项必须是布尔值"))
        result[name] = value
    for name, default, limit in (("scope", "all", 128), ("model", "", 200), ("target", "", 32), ("pdf_output_dir", "", 4096)):
        value = body.get(name, default)
        if value is None and name == "scope":
            value = default
        if not isinstance(value, str) or len(value) > limit:
            raise ImportTaskError(tr("导入参数不正确"))
        result[name] = value
    if result["target"]:
        result["target"] = langs.valid(result["target"])
    result["translation_extra"] = instructions.validate(body.get("translation_extra", ""))
    identity = body.get("request_id") or uuid4().hex
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", identity):
        raise ImportTaskError(tr("导入请求标识不正确"))
    return result, identity


class ImportTasks:
    LIMIT = 16

    def __init__(self, app):
        self.app = app
        self.scope = tasks.scope_id(app.lib.root)
        self.root = app.lib.root / ".research-tasks"
        self.root.mkdir(exist_ok=True)
        tasks.recover(app.lib.root)
        self.condition = threading.Condition(threading.RLock())
        self.pending = deque()
        self.events = {}
        self.held = set()
        self.worker = None
        self.closed = False
        # Restore visibility, never automatically repeat an uncertain import/model handoff.
        for task in self._records():
            if task["state"] in _ACTIVE or task.get("phase") == "interrupted":
                self._update(task["id"], state="interrupted", phase="interrupted", result_state="failed",
                             stage_label=tr("导入因服务重启而中断，请检查文献库后重试"))

    def _records(self):
        result = []
        for path in self.root.glob("*.json"):
            try:
                task = read_json(path, {})
            except (ValueError, OSError):
                log.exception("Unreadable import task retained: %s", path.name)
                continue
            if isinstance(task, dict) and task.get("kind") == "import" and task.get("scope") == self.scope and task.get("id") == path.stem:
                result.append(tasks.get(task["id"], scope=self.scope) or task)
        return result

    def list(self):
        with self.condition:
            records = sorted(self._records(), key=lambda t: t.get("created", 0), reverse=True)
            return [t for t in records if t["state"] in _ACTIVE] + [t for t in records if t["state"] not in _ACTIVE][:100]

    def get(self, identity):
        if not re.fullmatch(r"[a-f0-9]{32}", identity or ""):
            raise ImportTaskError(tr("找不到导入任务"), 404)
        task = tasks.get(identity, scope=self.scope) or read_json(self.root / (identity + ".json"), {})
        if not task or task.get("id") != identity or task.get("kind") != "import" or task.get("scope") != self.scope:
            raise ImportTaskError(tr("找不到导入任务"), 404)
        return task

    def _update(self, identity, **fields):
        with self.condition:
            if isinstance(fields.get("result"), dict) and fields["result"].get("id"):
                fields["paper_id"] = fields["result"]["id"]
            snapshot = tasks.update_external(identity, **fields)
            if snapshot is None:
                saved = self.get(identity)
                saved.update(fields, updated=time.time())
                snapshot = tasks.register_external(identity, "import", **{k: v for k, v in saved.items() if k not in ("id", "kind")})
            # Registry persistence is best-effort; acceptance and transitions here must be durable.
            write_json_atomic(self.root / (identity + ".json"), snapshot)
            return snapshot

    def submit(self, body, *, retry_of="", retry_pdf=False, paper_id=""):
        options, request_id = request_options(body)
        library_id = pwa.library_id(self.app.lib)
        if body.get("library_scope", self.scope) != self.scope or body.get("library_id", library_id) != library_id:
            raise ImportTaskError(tr("文献库已切换，请在当前文献库重新发起导入"), 409)
        options.update(library_scope=self.scope, library_id=library_id)
        identity = sha256((self.scope + ":" + request_id).encode()).hexdigest()[:32]
        with self.condition:
            try:
                previous = self.get(identity)
            except ImportTaskError as error:
                if error.status != 404:
                    raise
            else:
                if previous.get("request") != options:
                    raise ImportTaskError(tr("请求标识已被另一组导入参数使用"), 409)
                # Never acknowledge a memory-only record left by a failed disk write.
                write_json_atomic(self.root / (identity + ".json"), previous)
                return previous
            if self.closed:
                raise ImportTaskError(tr("服务正在关闭，请稍后重试"), 503)
            if len(self.held) >= self.LIMIT:
                raise ImportTaskError(tr("导入队列已满，请等待现有任务完成"), 429)
            self.app.location.begin()
            self.held.add(identity)
            try:
                snapshot = tasks.register_external(identity, "import", scope=self.scope, key=request_id,
                    request_id=request_id, request=options, retry_of=retry_of, retry_pdf=retry_pdf, paper_id=paper_id,
                    ref=options["ref"], title=options["ref"][:240],
                    stage_label=tr("等待导入"), message=tr("等待导入"))
                write_json_atomic(self.root / (identity + ".json"), snapshot)
                self.events[identity] = threading.Event()
                self.pending.append(identity)
                if self.worker is None or not self.worker.is_alive():
                    self.worker = threading.Thread(target=self._loop, name="browser-imports", daemon=True)
                    self.worker.start()
                self.condition.notify()
            except Exception:
                if identity in self.pending:
                    self.pending.remove(identity)
                self.events.pop(identity, None)
                tasks.update_external(identity, state="failed", error_reason=tr("导入任务未能保存，请重试"))
                self._release(identity)
                raise
            return snapshot

    def _release(self, identity):
        if identity in self.held:
            self.held.remove(identity)
            self.app.location.end()

    def cancel(self, identity):
        with self.condition:
            task = self.get(identity)
            if task["state"] not in _ACTIVE or task.get("phase") == "handoff":
                return task  # Accepted downstream work is not undone by cancelling its import notification.
            event = self.events.get(identity)
            if event:
                event.set()
            if identity in self.pending:
                self.pending.remove(identity)
                self.events.pop(identity, None)
                self._release(identity)
                return self._update(identity, state="cancelled", phase="cancelled", stage_label=tr("导入已取消"))
            return self._update(identity, state="cancelling", stage_label=tr("正在取消导入，等待当前网络请求结束"))

    def ack(self, identity):
        with self.condition:
            saved = self.get(identity)
            snapshot = tasks.acknowledge(identity, scope=self.scope) or {**saved, "acknowledged": True}
            write_json_atomic(self.root / (identity + ".json"), snapshot)
            return snapshot

    def retry(self, identity):
        with self.condition:
            old = self.get(identity)
            pdf_only = bool(old.get("retry_pdf") or (old.get("result_state") == "partial" and old.get("request", {}).get("pdf_export")))
            if old["state"] in _ACTIVE or (old["state"] == "completed" and not pdf_only):
                return old
            # Retrying a lost /retry response must return the same child, not spend twice.
            existing = next((t for t in self._records() if t.get("retry_of") == identity), None)
            if existing:
                return existing
            child = self.submit({**old["request"], "request_id": uuid4().hex}, retry_of=identity,
                                retry_pdf=pdf_only, paper_id=old.get("paper_id", ""))
            self._update(identity, retry_task_id=child["id"])
            return child

    def _loop(self):
        while True:
            with self.condition:
                while not self.pending and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                identity = self.pending.popleft()
                event = self.events[identity]
            try:
                self._run(identity, event)
            except Exception as error:
                # Disk/notification failures must not kill the queue worker.
                log.exception("Import task finalization failed: %s", identity)
                tasks.update_external(identity, state="failed", error_reason=str(error)[:500])

    def _run(self, identity, cancel):
        result = None
        try:
            with self.condition:
                if cancel.is_set() or self.closed:
                    raise CancelledError()
                task = self._update(identity, state="running", phase="fetch", stage_label=tr("正在查找并抓取内容"))
            options = task["request"]

            def progress(phase, done, total, message):
                with self.condition:
                    if cancel.is_set() or self.closed:
                        raise CancelledError()
                    self._update(identity, phase=phase, done=done, total=total, stage_label=message, message=message)

            if task.get("retry_pdf"):
                ws, fresh = self.app.lib.ws(task.get("paper_id", "")), False
                if not ws:
                    raise ImportTaskError(tr("已导入的文献不存在，请重新导入后生成 PDF"))
            else:
                ws, fresh = self.app.lib.import_reference(options["ref"], options["kind"], progress=progress, cancel=cancel)
            result = {"id": ws.id, "new": fresh, "queued": False}
            with self.condition:
                # The import can have committed a document just before cancellation. Keep it visible.
                if cancel.is_set() or self.closed:
                    raise CancelledError()
                self._update(identity, phase="handoff", result=result, stage_label=tr("文献已保存，正在安排后续处理"))
            # Past the cancellation boundary; do not hold the queue lock during export setup.
            if not task.get("retry_pdf"):
                result = finish_import(self.app, ws, fresh, options["translate"], scope=options["scope"],
                    read=options["read"], model=options["model"], target=options["target"], translation_extra=options["translation_extra"],
                    resume_processing=bool(task.get("retry_of")) and options["translate"] and not (ws.load("job") or {}).get("translate"))
            if options["pdf_export"]:
                try:
                    result["pdf_export"] = self.app.babeldoc.start(ws, model=options["model"], output_dir=options["pdf_output_dir"], regenerate=False)
                except Exception as error:
                    result["pdf_export_error"] = str(error)[:500]
            message = tr("文献已导入，PDF 导出未能启动") if result.get("pdf_export_error") else tr("导入完成；后续预处理或翻译另行进行")
            self._update(identity, state="completed", phase="completed", result=result, done=1, total=1,
                         result_state="partial" if result.get("pdf_export_error") else "completed",
                         error_reason=result.get("pdf_export_error", ""), stage_label=message, message=message)
        except CancelledError:
            self._update(identity, state="interrupted" if self.closed else "cancelled", phase="interrupted" if self.closed else "cancelled",
                         result=result, stage_label=tr("导入已中断，请检查文献库后重试") if self.closed else tr("导入已取消，已保存的文献会保留"))
        except Exception as error:
            if cancel.is_set() or self.closed:
                self._update(identity, state="interrupted" if self.closed else "cancelled", result=result,
                             stage_label=tr("导入已取消，已保存的文献会保留"))
            else:
                log.exception("Browser import failed: %s", identity)
                self._update(identity, state="failed", phase="failed", result=result, error_reason=str(error)[:500],
                             stage_label=tr("导入未完成，请查看原因"))
        finally:
            with self.condition:
                self.events.pop(identity, None)
                self._release(identity)

    def close(self):
        with self.condition:
            self.closed = True
            try:
                for identity, event in list(self.events.items()):
                    event.set()
                    if identity in self.pending:
                        try:
                            self._update(identity, state="interrupted", phase="interrupted", stage_label=tr("导入因服务重启而中断，请检查文献库后重试"))
                        except Exception:
                            log.exception("Could not persist interrupted import: %s", identity)
                        finally:
                            self.pending.remove(identity)
                            self.events.pop(identity, None)
                            self._release(identity)
            finally:
                self.condition.notify_all()


def manager(app):
    with _INIT:
        value = getattr(app, "import_tasks", None)
        if value is None:
            value = app.import_tasks = ImportTasks(app)
        return value

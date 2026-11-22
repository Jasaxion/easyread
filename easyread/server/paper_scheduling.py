"""Persisted two-stage paper scheduling; model execution stays in jobs.py."""
from __future__ import annotations

import queue
import threading
import time
from contextlib import nullcontext

from ..app import config
from ..app.i18n import tr
from ..app.log import log
from ..engines.engines import Cancelled, EngineError
from ..engines.timing import Timeline
from ..library.store import DataReadError, Workspace, now_iso
from ..translate import langs, translate, instructions
from .task_notices import notify


class PaperQueue(queue.Queue):
    """FIFO within each priority; a reservation promotes an existing entry once."""

    def __init__(self):
        super().__init__()
        self._priorities = set()

    def schedule(self, pid, priority=False):
        with self.not_empty:
            if pid in self.queue:
                if (pid in self._priorities) == priority:
                    return
                self.queue.remove(pid)
            else:
                self.unfinished_tasks += 1
            self._priorities.discard(pid)
            if priority:
                index = next((i for i, p in enumerate(self.queue) if p not in self._priorities), len(self.queue))
                self.queue.insert(index, pid)
                self._priorities.add(pid)
            else:
                self.queue.append(pid)
            self.not_empty.notify()

    def discard(self, pid):
        with self.mutex:
            if pid in self.queue:
                self.queue.remove(pid)
                self.unfinished_tasks -= 1
                if self.unfinished_tasks == 0:
                    self.all_tasks_done.notify_all()
            self._priorities.discard(pid)

    def _get(self):
        pid = super()._get()
        self._priorities.discard(pid)
        return pid


def phase_for(ws, job, recovering=False):
    if job.get("type") == "figure-images":
        return "model"
    # A running preparer may already have written page metadata. Its handoff
    # must wait for all remaining preparation work to finish.
    if not recovering and job.get("phase") in ("prepare", "model"):
        return job["phase"]
    if not job.get("translate") or not (ws.load("paper") or {}).get("meta", {}).get("pages"):
        return "prepare"
    return "model"


def phase_message(job):
    if job.get("phase") == "prepare":
        if job.get("state") == "running":
            return tr("正在预处理 PDF（已预约模型任务）") if job.get("translate") else tr("正在预处理 PDF")
        return tr("等待预处理（已预约模型任务）") if job.get("translate") else tr("等待预处理")
    if job.get("type") == "figure-images":
        return tr("译图排队中") if job.get("state") == "queued" else tr("正在开始任务")
    if job.get("state") == "queued":
        return tr("等待原文整理") if job.get("read") else tr("等待翻译")
    return tr("正在整理原文") if job.get("read") else tr("正在翻译")


def auto_translation_allowed(job):
    """Persisted permission, independent of the current global switch."""
    return (job.get("type") == "prepare" and not job.get("read") and not job.get("public_import")
            and job.get("allow_auto_translate") is True and not job.get("cancel_requested"))


def auto_translation_enabled(job, cfg=None):
    """An opt-in completion policy; explicit prepare-only requests always win."""
    if not auto_translation_allowed(job):
        return False
    cfg = config.load() if cfg is None else cfg
    return cfg.get("auto_translate_after_prepare") is True and cfg.get("engine", "none") != "none"


class PaperScheduling:
    def _schedule(self, ws):
        job = ws.load("job") or {}
        if job.get("phase") == "prepare":
            self.bulk.discard(ws.id)
            self.prepare.schedule(ws.id, priority=bool(job.get("translate")))
        else:
            self.prepare.discard(ws.id)
            self.bulk.schedule(ws.id)

    def _run_prepare(self, ws, job, cancel):
        if cancel.is_set():
            raise Cancelled()
        if not (ws.load("paper") or {}).get("meta", {}).get("pages"):
            timeline = Timeline()
            try:
                from . import public_network
                with timeline.stage("prepare"), public_network.importing() if job.get("public_import") else nullcontext():
                    translate.prepare(ws)
            finally:
                self._write(ws, timing=timeline.snapshot(), prepare_timing=timeline.snapshot())
        if cancel.is_set():
            raise Cancelled()

    # ---------- 整篇 ----------
    def _write(self, ws: Workspace, **fields):
        snapshot = {}
        def apply(job):
            job.update(fields)
            job["updated"] = now_iso()
            snapshot.update(job)
            snapshot["_notice_order"] = time.monotonic_ns()
        try:
            ws.update("job", apply)
            notify(getattr(self, "observer", None), "paper", ws.id, snapshot)
        except FileNotFoundError:
            # 删除会把整篇目录移到回收站；收尾时不再向旧路径写状态，也不重建目录。
            if ws.root.exists():
                raise

    def enqueue(self, ws: Workspace, pages: list[int] | None = None, translate_after: bool = True, scope: str | None = None,
                read: bool = False, model: str = "", confirmed: bool = False, target: str = "", cap_check: bool = False,
                translation_extra: str = "", public_import: bool = False, allow_auto_translate: bool = False):
        """pages=None：按 scope（all / body / range:A-B / first:N）翻译还没译的页。
        read：只读原文，用模型把页整理成段落、公式、表格，不翻译；之后再翻译时就地补中文。
        model：用“问 AI”名单里的哪个模型（导入时选的）；空着用设置里的翻译引擎。
        confirmed：只接受真正的 True；target：续跑时保留原任务的译文语言。
        cap_check：pages 来自系统计划而非用户指定，仍检查页数上限并跳过已完成页。"""
        target = langs.valid(target) if target else langs.of_paper((ws.load("paper") or {}).get("meta"))
        translation_extra = instructions.validate(translation_extra)
        snapshot, attempt = {}, {}
        def apply(job):
            active = job.get("state") in ("queued", "running")
            reserve = active and job.get("type") == "prepare" and translate_after
            if (active and not reserve) or (self.cancels.get(ws.id) and self.cancels[ws.id].is_set()):
                raise ValueError(tr("这篇论文已有任务在排队或运行，请等它结束，或先取消再重试。"))
            previous_state = job.get("state")
            public = public_import or (reserve and job.get("public_import", False))
            job.update(type="read" if read and translate_after else "translate" if translate_after else "prepare",
                       state="queued", message=tr("排队中"), pages=pages, scope=scope or "all", translate=translate_after, read=read, model=model or "",
                       confirmed=confirmed is True, target=target, cap_check=cap_check is True, translation_extra=translation_extra,
                       done=0, total=0, error="", failed={}, updated=now_iso(), usage={}, public_import=public,
                       allow_auto_translate=allow_auto_translate is True, cancel_requested=False)
            job["phase"] = "prepare" if reserve else phase_for(ws, job, recovering=True)
            if reserve:
                job["state"] = previous_state
            job["message"] = phase_message(job)
            # Explicit page requests can intentionally retranslate a completed page.
            # Its revision changes in the same paper.json write as the new translation.
            job.pop("retranslate_revisions", None)
            if pages is not None and not cap_check:
                tl = (ws.load("paper") or {}).get("translation", {})
                done = set(tl.get("done_pages", []))
                if not read:
                    done.difference_update(tl.get("en_pages", []))
                revisions = tl.get("page_revisions", {})
                job["retranslate_revisions"] = {str(n): revisions.get(str(n), 0) for n in pages if n in done}
            job.pop("page_cap", None)
            if not reserve:
                job.pop("timing", None)
                job.pop("prepare_timing", None)
            snapshot.update(job)
            snapshot["_notice_order"] = time.monotonic_ns()
            attempt["restart"] = not reserve and previous_state != "confirm"
        with self.lock:
            ws.update("job", apply)
            notify(getattr(self, "observer", None), "paper", ws.id, snapshot, restart=attempt["restart"])
            if (ws.load("job") or {}).get("state") == "queued":
                self._schedule(ws)

    def cancel(self, pid: str):
        with self.lock:
            ws = self.lib.ws(pid)
            job = (ws.load("job") or {}) if ws else {}
            if job.get("state") in ("queued", "running", "confirm"):
                self._write(ws, allow_auto_translate=False, cancel_requested=True)
            ev = self.cancels.get(pid)
            if ev:
                ev.set()
            if job.get("state") == "queued":
                self.bulk.discard(pid)
                self.prepare.discard(pid)
                self._write(ws, state="cancelled", message=tr("已取消"))
            elif job.get("state") == "confirm":
                self._write(ws, state="done", message=tr("已导入，未整理") if job.get("read") else tr("已导入，未翻译"))

    def enqueue_figures(self, ws: Workspace, body: dict):
        from . import figure_jobs
        if not (config.load().get("figure_images") or {}).get("enabled"):
            raise ValueError(tr("请先在设置中开启生成译图"))
        ids = figure_jobs.select(ws, body)
        snapshot = {}

        def apply(job):
            if job.get("state") in ("queued", "running"):
                raise ValueError(tr("这篇论文已有任务在排队或运行，请等它结束，或先取消再重试。"))
            job.update(type="figure-images", phase="model", state="queued", message=tr("译图排队中"),
                       figure_ids=ids, force=body.get("force", False), translate=True, read=False,
                       model="", target="", translation_extra="", done=0, total=len(ids),
                       error="", failed={}, image_failed={}, updated=now_iso(), usage={},
                       allow_auto_translate=False, cancel_requested=False)
            job.pop("timing", None)
            snapshot.update(job)
            snapshot["_notice_order"] = time.monotonic_ns()
        with self.lock:
            ws.update("job", apply)
            notify(getattr(self, "observer", None), "paper", ws.id, snapshot, restart=True)
            self._schedule(ws)
        return {"ok": True, "count": len(ids)}

    def stop_model_work(self, ids: list[str] | None = None) -> int:
        """一键停止：只停用模型的部分（翻译、整理原文、等确认页数的），原页照样渲染。
        还在排队、原页没渲染的改成只渲染；ids 为空就是全部。返回停了几篇。"""
        want = set(ids) if ids is not None else None
        n = 0
        with self.lock:
            for ws in self.lib.all():
                if want is not None and ws.id not in want:
                    continue
                job = ws.load("job") or {}
                state = job.get("state")
                if state not in ("queued", "running", "confirm") or not (job.get("translate") or auto_translation_allowed(job)):
                    continue
                n += 1
                self._write(ws, allow_auto_translate=False,
                            cancel_requested=bool(job.get("cancel_requested")) or phase_for(ws, job) != "prepare" or state == "confirm")
                if phase_for(ws, job) == "prepare" and state != "confirm":
                    self._write(ws, type="prepare", phase="prepare", translate=False, read=False, allow_auto_translate=False,
                                message=tr("正在预处理 PDF") if state == "running" else tr("等待预处理"))
                    if state == "queued":
                        self._schedule(ws)
                elif state == "running":
                    ev = self.cancels.get(ws.id)
                    if ev:
                        ev.set()
                elif state == "confirm":
                    self._write(ws, state="done", message=tr("已导入，未整理") if job.get("read") else tr("已导入，未翻译"))
                elif job.get("type") == "figure-images" or (ws.load("paper") or {}).get("meta", {}).get("pages"):
                    self.bulk.discard(ws.id)
                    self._write(ws, state="cancelled", message=tr("已取消"))
        return n

    def _resume(self):
        for ws in self.lib.all():
            try:
                job = ws.load("job") or {}
                if job.get("cancel_requested") and job.get("state") in ("queued", "running"):
                    self._write(ws, state="cancelled", allow_auto_translate=False, message=tr("已取消"))
                    continue
                phase = phase_for(ws, job, recovering=True) if job.get("state") in ("queued", "running") else None
            except DataReadError as e:
                # Preserve unreadable state for recovery; other papers can still resume.
                log.exception("Cannot resume task; unreadable data file: %s", e.path)
                continue
            if job.get("state") == "confirm":
                notify(getattr(self, "observer", None), "paper", ws.id, job)
            if job.get("state") in ("queued", "running"):
                if job.get("type") == "figure-images" and job.get("state") == "running":
                    self._write(ws, state="cancelled", message=tr("译图任务因服务重启而中断，已保存的图片保留；可手动补齐"))
                    continue  # An in-flight generation may have consumed quota; never blindly repeat it.
                # Legacy running tasks persisted their original page list without a checkpoint.
                if job.get("state") == "running" and "retranslate_revisions" not in job:
                    self._write(ws, retranslate_revisions={})
                self._write(ws, state="queued", phase=phase, message=phase_message({**job, "phase": phase, "state": "queued"}))
                self._schedule(ws)

    def _bulk_loop(self):
        while True:
            pid = self.bulk.get()
            try:
                self._process_bulk(pid)
            except Exception:  # noqa: BLE001
                # 启动或收尾（包括写错误状态）也可能失败，不能让一篇论文停掉整条队列。
                log.exception("后台任务出错 %s", pid)
            finally:
                self.bulk.task_done()

    def _prepare_loop(self):
        while True:
            pid = self.prepare.get()
            try:
                self._process_bulk(pid, "prepare")
            except Exception:  # noqa: BLE001
                log.exception("PDF preparation task failed %s", pid)
            finally:
                self.prepare.task_done()

    def _process_bulk(self, pid: str, phase: str = "model"):
        with self.lock:
            ws = self.lib.ws(pid)
            if not ws:
                return
            job = ws.load("job") or {}
            if job.get("state") != "queued" or job.get("cancel_requested") or phase_for(ws, job) != phase:
                return
            cancel = self.cancels[pid] = threading.Event()
            try:
                self._write(ws, state="running", phase=phase, message=phase_message({**job, "phase": phase, "state": "running"}))
            except Exception:
                self.cancels.pop(pid, None)
                raise
        handoff = False
        try:
            if phase == "prepare":
                self._run_prepare(ws, job, cancel)
                handoff = True
            else:
                self._run_bulk(ws, job, cancel)
        except Cancelled:
            self._write(ws, state="cancelled", message=tr("已取消，已译的部分保留"))
        except Exception as e:  # noqa: BLE001
            if cancel.is_set():  # 点了停止后模型调用被打断而报错：算取消，不算出错
                self._write(ws, state="cancelled", message=tr("已取消，已译的部分保留"))
                return
            msg = str(e) if isinstance(e, (EngineError, KeyError, ValueError)) else f"{type(e).__name__}: {e}"
            self._write(ws, state="error", message=tr("出错了"), error=msg[:800])
            log.exception("后台任务出错 %s", pid)
            try:
                translate.journal(ws, tr("出错停止：{msg}", msg=msg[:500]))
            except OSError:
                pass
        finally:
            with self.lock:
                # An immediate retry may already have registered its own event.
                if self.cancels.get(pid) is cancel:
                    self.cancels.pop(pid, None)
                    if handoff and ws.root.exists():
                        latest = ws.load("job") or {}
                        auto_fields = {}
                        if not cancel.is_set() and not latest.get("translate") and auto_translation_enabled(latest):
                            auto_fields = {"type": "translate", "translate": True, "read": False}
                            latest = {**latest, **auto_fields}
                        if cancel.is_set():
                            self._write(ws, state="cancelled", message=tr("已取消，已译的部分保留"))
                        elif latest.get("translate"):
                            self._write(ws, **auto_fields, phase="model", state="queued",
                                        message=phase_message({**latest, "phase": "model", "state": "queued"}))
                            self._schedule(ws)
                        else:
                            self._write(ws, state="done", message=tr("已导入"))

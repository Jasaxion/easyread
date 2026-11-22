"""后台任务：导入后的渲染与整篇翻译（独立队列，状态落在每篇的 job.json，重启后接着做），
以及“让模型回答我的问题 / 重译这段”这类小任务（另一条队列，不用等整篇翻译）。"""
from __future__ import annotations

import queue
import threading
import time
from uuid import uuid4

from ..chat import chat_models
from ..app import config
from ..translate import langs, translate, instructions
from ..engines import usage
from ..engines.timing import Timeline
from ..engines.engines import Cancelled, EngineError
from ..app.i18n import tr
from ..library.library import Library
from ..app.log import log
from ..library.store import DataReadError, Workspace, now_iso
from .task_notices import notify
from .paper_workers import worker_count
from .paper_scheduling import PaperScheduling, PaperQueue


def engine_for(cfg: dict, model: str | None) -> dict:
    """导入时选了“问 AI”名单里的模型就用它，否则用设置里的翻译引擎。名单里已经没有这个模型了，也用翻译引擎。"""
    m = next((x for x in chat_models.models(cfg) if x.get("id") == model), None) if model else None
    if not m:
        return cfg
    out, _ = chat_models.engine_cfg(cfg, model)
    o = cfg.get("openai") or {}
    if m["engine"] == "openai" and m.get("preset") == o.get("preset") and (m.get("model") or o.get("model")) == o.get("model"):
        out["openai"]["vision"] = o.get("vision", False)  # 和翻译引擎是同一个模型：沿用“模型能看图”
    return out


class Jobs(PaperScheduling):
    def __init__(self, lib: Library, observer=None):
        self.lib = lib
        self.observer = observer
        self.bulk = PaperQueue()  # Model-backed paper work only.
        self.prepare = PaperQueue()
        self.small: queue.Queue[dict] = queue.Queue()
        self.cancels: dict[str, threading.Event] = {}
        self.small_cancels: dict[str, threading.Event] = {}
        self.recent: list[dict] = []  # 小任务的状态，给页面轮询
        self.lock = threading.Lock()
        self._resume()
        threading.Thread(target=self._small_loop, daemon=True).start()
        threading.Thread(target=self._prepare_loop, daemon=True).start()
        for _ in range(worker_count(config.load())):
            threading.Thread(target=self._bulk_loop, daemon=True).start()

    def _run_bulk(self, ws: Workspace, job: dict, cancel: threading.Event):
        if job.get("type") == "figure-images":
            from . import figure_jobs
            return figure_jobs.run(self, ws, job, config.load(), cancel)
        cfg = engine_for(config.load(), job.get("model"))
        timeline = Timeline()
        cfg = {**cfg, "translation_extra": job.get("translation_extra", "")}
        if job.get("target"):
            cfg = {**cfg, "target": langs.valid(job["target"])}
        if cancel.is_set():
            raise Cancelled()
        read = bool(job.get("read"))
        if read and ws.load("paper").get("meta", {}).get("source_type") == "web":
            self._write(ws, state="done", message=tr("已导入"))
            return
        if not job.get("translate") or cfg.get("engine") == "none":
            self._write(ws, state="done", message=tr("已导入（没有可用的模型，先放原页）") if read else tr("已导入（未开启自动翻译）") if job.get("translate") else tr("已导入"))
            return
        paper = ws.load("paper")
        all_pages = [p["n"] for p in paper["meta"]["pages"]]
        tl = paper.get("translation", {})
        # 只读原文：跳过已经整理过（或已经译过）的页；翻译：跳过已经有译文的页，只有原文的页会补译文
        skip = set(tl.get("done_pages", [])) if read else set(tl.get("done_pages", [])) - set(tl.get("en_pages", []))
        planned = job.get("pages") is not None
        wanted = job["pages"] if planned else translate.scope_pages(ws, job.get("scope")) or all_pages
        baseline = job.get("retranslate_revisions")
        if planned and baseline is not None:
            revisions = tl.get("page_revisions", {})
            pages = [n for n in wanted if n not in skip or
                     (str(n) in baseline and baseline[str(n)] == revisions.get(str(n), 0))]
        else:
            # Legacy queued explicit requests may not have started yet.
            pages = wanted if planned and not job.get("cap_check") else [n for n in wanted if n not in skip]
        if not pages:
            self._write(ws, state="done", message=tr("选定范围已整理完") if read else tr("选定范围已译完"))
            return

        cap = cfg.get("page_cap", 60)
        if type(cap) is not int or cap < 0:
            cap = 60  # 手改配置写错时仍按默认上限保护，不把错误值当作不限。
        check_cap = job.get("cap_check") or (not planned and (job.get("scope") or "all") in ("all", "body"))
        if cap > 0 and len(pages) > cap and check_cap and job.get("confirmed") is not True:
            with self.lock:
                if cancel.is_set():
                    raise Cancelled()
                self._write(ws, state="confirm", total=len(pages), page_cap=cap, pages=pages, cap_check=True,
                            message=tr("这篇要整理 {n} 页，超过了你设的 {cap} 页，确认后再开始", n=len(pages), cap=cap) if read
                            else tr("这篇要译 {n} 页，超过了你设的 {cap} 页，确认后再开始", n=len(pages), cap=cap))
            return

        meter = usage.Meter(cfg.get("engine") or "")
        meter.timing = timeline

        def report(done, total, message):
            if cancel.is_set():
                raise Cancelled()
            self._write(ws, state="running", done=done, total=total, message=message, usage=meter.snapshot())

        started = time.time()
        try:
            failed = translate.translate_pages(ws, cfg, pages, cancel, report, meter, read)
        finally:  # 取消、出错也把已经花掉的记上
            run = meter.snapshot()
            self._write(ws, timing=timeline.snapshot())
            if run["calls"]:
                ws.update("job", lambda j: j.update(usage=run, usage_total=usage.merge(j.get("usage_total"), run)))
        minutes = max(1, round((time.time() - started) / 60))
        if failed:
            first = next(iter(failed.values()))
            self._write(ws, state="partial", failed={str(k): v for k, v in failed.items()}, error=first,
                        message=(tr("{ok} 页好了，", ok=len(pages) - len(failed)) if len(failed) < len(pages) else "")
                        + (tr("{n} 页没整理成功", n=len(failed)) if read else tr("{n} 页没译成功", n=len(failed))))
        else:
            self._write(ws, state="done", failed={}, error="",
                        message=tr("原文整理完成（{n} 页，用时约 {m} 分钟）", n=len(pages), m=minutes) if read
                        else tr("翻译完成（{n} 页，用时约 {m} 分钟）", n=len(pages), m=minutes))

    # ---------- 小任务 ----------
    def submit_small(self, kind: str, pid: str, **kw) -> dict:
        job = {"id": "j" + uuid4().hex, "kind": kind, "pid": pid, "state": "queued", "message": tr("排队中"),
               "done": 0, "total": 0, "at": now_iso(), **kw}
        with self.lock:
            self.recent = ([job] + self.recent)[:50]
        cancel_evt = self.small_cancels.setdefault(job["id"], threading.Event())
        if kind == "smart-annotate":
            try:
                from ..research import tasks as rtasks
                scope = rtasks.scope_id(self.lib.root)
                rtasks.register_external(job["id"], "smart-annotate", scope=scope, key=pid, paper_id=pid,
                                         title=kw.get("title", ""), cancel_event=cancel_evt)
            except Exception:
                pass
        notify(getattr(self, "observer", None), "small", job["id"], job)
        self.small.put(job)
        return job

    def cancel_small(self, job_id: str) -> bool:
        """取消仍在執行的小任務；已寫入的候選會保留。"""
        with self.lock:
            job = next((x for x in self.recent if x.get("id") == job_id), None)
            if not job or job.get("state") not in ("queued", "running"):
                return False
            self.small_cancels.setdefault(job_id, threading.Event()).set()
            job["message"] = tr("正在取消")
            if job.get("kind") == "smart-annotate":
                try:
                    from ..research import tasks as rtasks
                    rtasks.update_external(job_id, state="cancelling", phase="cancelling", message=tr("正在取消工作…"))
                except Exception:
                    pass
            return True

    def small_status(self, pid: str | None = None) -> list[dict]:
        with self.lock:
            return [dict(j) for j in self.recent if not pid or j["pid"] == pid]

    def busy(self) -> bool:
        """还有整篇翻译或小任务没做完（关页自动退出时要等它们）。"""
        if self.cancels or not self.bulk.empty() or not self.prepare.empty() or not self.small.empty():
            return True
        with self.lock:
            return any(j["state"] in ("queued", "running") for j in self.recent)

    def _small_loop(self):
        from .small_jobs import run
        run(self)

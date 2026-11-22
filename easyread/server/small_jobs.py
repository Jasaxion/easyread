"""Execute the existing small-job queue, including incremental smart annotations."""
from ..app import config
from ..app.i18n import tr
from ..app.log import log
from ..engines.engines import Cancelled
from ..translate import translate
import threading
from .task_notices import notify


def run(jobs):
    while True:
        job = jobs.small.get()
        ws = jobs.lib.ws(job["pid"])
        if not ws:
            job["state"], job["message"] = "error", tr("文献已移除，无法执行任务")
            jobs.small_cancels.pop(job["id"], None)
            if job["kind"] == "smart-annotate":
                from ..research import tasks
                tasks.update_external(job["id"], state="failed", phase="failed", message=job["message"])
            notify(getattr(jobs, "observer", None), "small", job["id"], job)
            continue
        cancel = jobs.small_cancels.setdefault(job["id"], threading.Event())
        job["state"], job["message"] = "running", tr("模型思考中")
        notify(getattr(jobs, "observer", None), "small", job["id"], job)
        if job.get("kind") == "smart-annotate":
            try:
                from ..research import tasks as rtasks
                rtasks.update_external(job["id"], state="running", phase="running", message=tr("模型思考中"))
            except Exception:
                pass
        try:
            if cancel.is_set():
                raise Cancelled()
            cfg = config.load()
            if job["kind"] == "answer":
                translate.answer(ws, cfg, job["note"], None)
            elif job["kind"] == "retranslate":
                translate.retranslate(ws, cfg, job["key"], job.get("hint", ""), None)
            elif job["kind"] == "smart-annotate":
                from ..chat import smart_annotations

                def on_progress(done_chunks: int, total_chunks: int, count: int) -> None:
                    with jobs.lock:
                        job["done"] = done_chunks
                        job["total"] = total_chunks
                        job["saved"] = count
                        if total_chunks > 0:
                            if done_chunks < total_chunks:
                                job["message"] = f"正在分析第 {done_chunks + 1} / {total_chunks} 批..."  # i18n-ok
                            else:
                                job["message"] = tr("完成")
                        if job.get("kind") == "smart-annotate":
                            try:
                                from ..research import tasks as rtasks
                                rtasks.update_external(job["id"], done=done_chunks, total=total_chunks, message=job["message"])
                            except Exception:
                                pass

                result = smart_annotations.generate(
                    ws, scope=job.get("scope", "all"), section_id=job.get("section_id", ""),
                    mode=job.get("mode", "auto"), purpose=job.get("purpose", ""),
                    intensity=job.get("intensity", "standard"),
                    model_id=job.get("model", ""), cancel=cancel,
                    on_progress=on_progress,
                )
                job["result"] = result
                job["done"] = job.get("total", 0)
            if cancel.is_set():
                raise Cancelled()
            job["state"], job["message"] = "done", tr("完成")
            if job.get("kind") == "smart-annotate":
                try:
                    from ..research import tasks as rtasks
                    rtasks.update_external(job["id"], state="completed", phase="completed", done=job.get("total", 0),
                                           message=tr("完成"), result=job.get("result"))
                except Exception:
                    pass
        except Cancelled:
            job["state"], job["message"] = "cancelled", tr("已取消")
            if job.get("kind") == "smart-annotate":
                try:
                    from ..research import tasks as rtasks
                    rtasks.update_external(job["id"], state="cancelled", phase="cancelled", message=tr("已取消"))
                except Exception:
                    pass
        except Exception as e:  # noqa: BLE001
            job["state"], job["message"] = "error", str(e)[:500]
            if job.get("kind") == "smart-annotate":
                try:
                    from ..research import tasks as rtasks
                    rtasks.update_external(job["id"], state="failed", phase="failed", message=str(e)[:500])
                except Exception:
                    pass
            log.exception("小任务出错 %s", job.get("kind"))
        finally:
            notify(getattr(jobs, "observer", None), "small", job["id"], job)
            with jobs.lock:
                jobs.small_cancels.pop(job["id"], None)

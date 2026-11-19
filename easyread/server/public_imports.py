"""Small durable import queue; rendering/translation stay in the existing Jobs queue."""
import queue
import re
import threading
from uuid import uuid4

from ..library.store import now_iso, read_json, write_json_atomic
from ..translate import langs
from ..app.log import log
from ..pdf.sources import SourceError
from .public_api import APIError
from . import public_network

MAX_UPLOAD = 50 * 1024 * 1024


def failure(exc):
    chain = []
    while exc is not None and len(chain) < 8:
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    for item in chain:
        if isinstance(item, public_network.NetworkError):
            return {"code": "import_network_rejected", "message": str(item)}
        if isinstance(item, TimeoutError):
            return {"code": "download_timeout", "message": "Import download timed out"}
    if any(isinstance(item, SourceError) for item in chain):
        return {"code": "source_unavailable", "message": "Source could not be downloaded; check availability or upload a PDF"}
    if isinstance(chain[0], ValueError):
        return {"code": "unsupported_source", "message": "Source is invalid or needs JavaScript; upload a PDF instead"}
    return {"code": "import_failed", "message": "Import failed; check the server log"}


class Imports:
    def __init__(self, api):
        self.api, self.app = api, api.app
        self.root = self.app.lib.root / ".open-api-tasks"
        self.root.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.queue = queue.Queue(maxsize=8)
        self.stopped = threading.Event()
        # Never silently repeat a download or an uncertain import after restart.
        for path in self.root.glob("*.json"):
            task = read_json(path, {})
            if task.get("state") in ("queued", "running"):
                task.update(state="interrupted", error={"code": "interrupted", "message": "Server restarted during import"}, updated=now_iso())
                write_json_atomic(path, task)
        for pattern in ("*.pdf", "*.html.json"):
            for path in self.root.glob(pattern):
                path.unlink(missing_ok=True)
        threading.Thread(target=self._loop, daemon=True, name="public-imports").start()

    def close(self):
        self.stopped.set()

    def submit(self, *, url=None, data=None, filename="paper.pdf", translate=False, target="", kind="auto",
               html=None, title="", source_id=""):
        if sum(value is not None for value in (url, data, html)) != 1:
            raise APIError("invalid_parameter", "Supply exactly one import source")
        if type(translate) is not bool:
            raise APIError("invalid_parameter", "translate must be a boolean")
        if not isinstance(target, str) or (target and not langs.normalize(target)):
            raise APIError("invalid_parameter", "Unsupported translation target")
        if kind not in ("auto", "pdf", "web"):
            raise APIError("invalid_parameter", "kind must be auto, pdf or web")
        if html is not None:
            from ..weblog.upload import validate, MAX_HTML
            if isinstance(html, str) and len(html.encode("utf-8")) > MAX_HTML:
                raise APIError("payload_too_large", "HTML exceeds 8 MiB", 413)
            try:
                validate(html, title, source_id)
            except ValueError as exc:
                raise APIError("invalid_parameter", str(exc)) from None
            kind = "html"
        elif url is not None:
            if not isinstance(url, str) or not url or len(url) > 4096:
                raise APIError("invalid_parameter", "url must be a nonempty HTTP(S) URL")
            try:
                public_network.validate_url(url)
            except ValueError as exc:
                raise APIError("unsafe_url", str(exc)) from None
        elif not data or len(data) > MAX_UPLOAD or not data.startswith(b"%PDF"):
            raise APIError("invalid_file", "Expected a PDF up to 50 MiB")
        filename = re.split(r"[/\\]", filename)[-1][:200] or "paper.pdf"
        with self.lock:
            if self.stopped.is_set():
                raise APIError("unavailable", "Server is shutting down", 503)
            if self.queue.full():
                raise APIError("queue_full", "Import queue is full", 429)
            try:
                self.app.location.begin()
            except ValueError as exc:
                raise APIError("library_unavailable", str(exc), 409) from None
            tid = uuid4().hex
            task = {"id": tid, "state": "queued", "created": now_iso(), "updated": now_iso(),
                    "document_id": None, "error": None}
            try:
                if data is not None:
                    (self.root / f"{tid}.pdf").write_bytes(data)
                if html is not None:
                    write_json_atomic(self.root / f"{tid}.html.json", {"html": html, "title": title, "source_id": source_id})
                write_json_atomic(self.root / f"{tid}.json", task)
                self.queue.put_nowait((tid, url, filename, translate, target, kind))
            except Exception:
                (self.root / f"{tid}.pdf").unlink(missing_ok=True)
                (self.root / f"{tid}.html.json").unlink(missing_ok=True)
                self.app.location.end()
                raise
        return {"task_id": tid, "state": "queued", "status_url": f"{self.api.base_url}/api/v1/tasks/{tid}"}

    def _update(self, tid, **fields):
        with self.lock:
            path = self.root / f"{tid}.json"
            task = read_json(path, {})
            task.update(fields, updated=now_iso())
            write_json_atomic(path, task)

    def get(self, tid):
        if not isinstance(tid, str) or not re.fullmatch(r"[0-9a-f]{32}", tid):
            raise APIError("not_found", "Task not found", 404)
        with self.lock:
            task = read_json(self.root / f"{tid}.json", None)
            if task is None:
                raise APIError("not_found", "Task not found", 404)
            if task.get("document_id"):
                ws = self.app.lib.ws(task["document_id"])
                if ws:
                    # `state` tracks importing; `processing` separately reports the
                    # document's current preparation/translation job (including confirm).
                    job = ws.load("job") or {}
                    task["processing"] = {k: job[k] for k in ("type", "state", "done", "total", "error", "message", "updated") if k in job} if job else None
                    task["links"] = self.api.links(ws.id)
            return task

    def _loop(self):
        while not self.stopped.is_set():
            try:
                work = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            tid, url, filename, translate, target, kind = work
            try:
                self._update(tid, state="running")
                with public_network.importing():
                    if kind == "html":
                        ws, fresh = self.app.lib.create_from_html(**read_json(self.root / f"{tid}.html.json", {}))
                    elif url is not None:
                        ws, fresh = self.app.lib.import_reference(url, kind)
                    else:
                        ws, fresh = self.app.lib.create_from_pdf((self.root / f"{tid}.pdf").read_bytes(), filename)
                self._update(tid, document_id=ws.id, new=fresh)
                paper, job = ws.load("paper") or {}, ws.load("job") or {}
                active = job.get("state") in ("queued", "running")
                queued = (fresh or not paper.get("meta", {}).get("pages")) and not active
                if kind == "html" and not translate:
                    # Already structured; no preparation, cover browser or model needed.
                    queued = False
                if queued:
                    if translate:
                        langs.remember(ws, target)
                    self.app.jobs.enqueue(ws, translate_after=translate, target=target, public_import=True)
                self._update(tid, state="done", document_id=ws.id, new=fresh, queued=queued)
            except Exception as exc:
                log.exception("Public import failed: %s", tid)
                self._update(tid, state="error", error=failure(exc))
            finally:
                (self.root / f"{tid}.pdf").unlink(missing_ok=True)
                (self.root / f"{tid}.html.json").unlink(missing_ok=True)
                self.app.location.end()
                self.queue.task_done()

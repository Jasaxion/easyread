"""HTTP 服务：默认只监听本机，容器部署显式开放。文献库页、阅读页、导入、后台任务。"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import threading

from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .. import __version__
from ..chat import answer_styles, chat, chat_models, chat_store, notehelp
from ..pdf import figures, pdfwork
from ..app import cli_models, config, i18n, open_link, prefs, updates
from ..engines import detect, engines, usage
from ..translate import langs
from ..library import citations, trash
from . import settings_api, library_api, jobs_api, paper_api, research_api, wsock, network, access_routes, qa_cli_api
from .access import Access
from . import public_routes, pwa, static_files, import_routes
from .import_flow import finish_import
from .public_api import PublicAPI
from .terminal import Terminals
from .babeldoc_api import BabelDocJobs
from ..app.log import log, tail
from ..bulk import api as bulk_api
from .jobs import Jobs
from ..library.library import Library, sha256_bytes
from ..app.i18n import tr
from ..library.store import now_iso
from ..library.reader_files import prepare_later as _prepare_later

WEB = config.WEB
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")
MAX_UPLOAD = 200 * 1024 * 1024


def _safe(base: Path, rel: str) -> Path | None:
    target = (base / rel).resolve()
    return target if target.is_relative_to(base.resolve()) and target.is_file() else None


class App:
    def __init__(self, cfg: dict):
        self.access = Access((config.library_dir(cfg) if config.temp_library() else config.HOME) / ".access.json")
        self.terminals = Terminals(self.access)
        self.access.changed = self.terminals.close
        self.lib = Library(config.library_dir(cfg))
        self.jobs = Jobs(self.lib)
        self.babeldoc = BabelDocJobs()
        self.token = os.urandom(12).hex()
        self.location = library_api.LibraryLocation(self)
        self.public_api = PublicAPI(self, cfg)
        research_api.recover(self)
        self.import_tasks = None  # Started lazily for browser URL imports.
        self.presence = None  # presence.Presence，serve() 里设


class Handler(BaseHTTPRequestHandler):
    app: App
    protocol_version = "HTTP/1.1"
    allowed_hosts = network.LOOPBACK_HOSTS

    def _host_ok(self):
        if network.check_host(self.headers, self.allowed_hosts):
            return True
        self.close_connection = True
        self._json(403 if self.headers.get("Host") else 400, {"error": "bad host"})
        return False

    def end_headers(self):
        if getattr(self, "access_cookie", None):
            self.send_header("Set-Cookie", self.access_cookie)
            self.access_cookie = None
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, fmt, *args):
        if args and str(args[1]).startswith(("4", "5")):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---------- 输出 ----------
    def _send(self, code: int, body: bytes, ctype: str, cache: bool = False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if ctype.startswith("text/html"):
            self.send_header("X-EasyRead-Web-Build", pwa.build_id())
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _file(self, path: Path | None, cache=False, ctype=None):
        if not path:
            return self._json(404, {"error": "not found"})
        ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype, cache)

    def _redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _page(self, path: Path):
        self._send(200, i18n.inject(path.read_text(encoding="utf-8")).encode("utf-8"), "text/html; charset=utf-8")

    def _download(self, body: bytes, filename: str, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f"attachment; filename=\"export.html\"; filename*=UTF-8''{quote(filename)}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _chat(self, ws, body):
        from .chat_handler import handle
        return handle(self, ws, body)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length", "0"))
        if n > MAX_UPLOAD:
            raise ValueError(tr("文件太大"))
        return self.rfile.read(n) if n else b""

    def _import_result(self, ws, fresh, translate_after, scope, read=False, model="", target="", translation_extra="", citation=None):
        return self._json(200, finish_import(self.app, ws, fresh, translate_after, scope, read, model, target, translation_extra, citation))

    # ---------- GET ----------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if public_routes.handle(self):
            return
        if not self._host_ok() or not access_routes.guard(self):
            return
        try:
            with self.app.location.request(self.command, self.path):
                self._get()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Back/close/timeout can cancel a read after headers are sent. The
            # client is gone; a second error response only raises again.
            self.close_connection = True
        except ValueError as e:
            self._json(409, {"error": str(e), "library_status": self.app.location.status})
        except Exception as e:  # noqa: BLE001
            log.exception("请求出错 %s", self.path)
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _get(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        app, lib = self.app, self.app.lib
        if pwa.get(self, path) or access_routes.get(self, path) or import_routes.get(self, path) or qa_cli_api.handle(self):
            return
        research = research_api.get(app, path + ("?" + url.query if url.query else ""))
        if research is not None:
            return self._json(200, research)
        if path == "/research":
            return self._page(WEB / "research.html")
        if path in ("/", "/index.html"):
            return self._page(WEB / "library.html")
        if path in ("/babeldoc", "/web/babeldoc.html"):
            return self._redirect("/")
        if path == "/web/tabs.html":
            return self._page(WEB / "tabs.html")
        if path.startswith("/read/"):
            return self._page(WEB / "reader.html")
        if path == "/open":  # 外部工具打开指定论文/段落；桌面版的 easyread://open 也转到这里
            return self._redirect(open_link.target(lib, parse_qs(url.query)))
        if path == "/api/version":
            return self._json(200, open_link.version())
        if path.startswith("/web/"):
            return static_files.send(self, _safe(WEB, path[5:]))
        if path == "/api/presence" and wsock.is_upgrade(self.headers):
            return self._presence()
        location = library_api.get(app, path)
        if location is not None:
            return self._json(200, location)
        if path in bulk_api.GET:
            return self._json(200, bulk_api.get(app, path))
        if path == "/api/library":
            return self._json(200, jobs_api.library_state(app))
        if path == "/api/update":  # 有没有新版本（一天最多问一次 GitHub）
            return self._json(200, updates.check(force=parse_qs(url.query).get("force") == ["1"]))
        if path == "/api/trash":
            return self._json(200, {"items": [{**item, "type": "paper"} for item in trash.items(lib.root)] + research_api.trash_items(lib.root)})
        if path == "/api/config":
            return self._json(200, {"config": config.public(config.load()), "presets": config.PRESETS, "groups": config.PRESET_GROUPS})
        if path == "/api/engines":
            cfg = config.load()
            found = detect.detect(cfg, fresh=parse_qs(url.query).get("fresh") == ["1"])
            return self._json(200, {"found": found, "sources": detect.cli_sources(found), "ready": detect.ready(cfg, found), "engine": cfg.get("engine"), "models": cli_models.listing()})
        if path == "/api/prefs":
            return self._json(200, prefs.load())
        if path == "/api/chat/models":
            return self._json(200, chat_models.listing(config.load()))
        if path == "/api/log":
            return self._json(200, {"text": tail(config.LOG_PATH, 200), "path": str(config.LOG_PATH)})
        if path == "/api/jobs":
            return self._json(200, {"jobs": app.jobs.small_status(parse_qs(url.query).get("pid", [None])[0])})
        if path.startswith("/api/p/"):
            parts = path.split("/")  # ['', 'api', 'p', id, action, name?]
            ws = lib.ws(parts[3]) if len(parts) > 4 else None
            if not ws:
                return self._json(404, {"error": tr("没有这篇论文")})
            action = parts[4]
            if action == "babeldoc":
                if len(parts) == 5:
                    return self._json(200, app.babeldoc.status(ws))
                if len(parts) == 7:
                    return self._file(app.babeldoc.file(ws, parts[5], parts[6]), ctype="application/pdf")
            if action == "citations":
                return self._json(200, citations.graph_for(lib, ws.id))
            if action == "state":
                opened = {"last_opened": now_iso()}
                if (ws.load("item") or {}).get("status", "unread") == "unread":  # 打开过就算在读
                    opened["status"] = "reading"
                if app.location.status == "idle":
                    ws.patch_item(opened)
                if app.location.status == "idle":
                    research_api.refresh_annotations(ws)
                # Take versions before reading content: concurrent preparation
                # can then trigger a later poll, never tag old data as current.
                versions = ws.versions()
                cfg = config.load()
                state = {**{n: ws.load(n) for n in ("paper", "discussion", "reader", "layout", "item", "job")},
                         "versions": versions, "library_id": pwa.library_id(lib), "token": app.token, "id": ws.id, "library_status": app.location.status,
                         "engine": cfg.get("engine"), "figure_images_enabled": (cfg.get("figure_images") or {}).get("enabled") is True}
                state["job"] = jobs_api.job_state(state["job"], cfg)
                self._json(200, state)
                # Existing layout is usable immediately. Upgrade only after the
                # response has been sent; polling picks up the new layout/crops.
                if app.location.status == "idle" and (state["job"] or {}).get("state") not in ("queued", "running"):
                    try:
                        _prepare_later(ws, app.location)
                    except Exception:  # noqa: BLE001
                        # The 200 response is already sent; do not append a 500
                        # response if migration or thread registration now fails.
                        log.exception("启动阅读页准备失败 %s", ws.root)
                return
            if action == "versions":
                cfg = config.load()
                pending = jobs_api.job_state(ws.load("job"), cfg).get("auto_translate_pending", False)
                return self._json(200, {**ws.versions(), "library_id": pwa.library_id(lib), "library_status": app.location.status,
                                        "auto_translate_pending": pending,
                                        "figure_images_enabled": (cfg.get("figure_images") or {}).get("enabled") is True})
            if action == "chat":
                return self._json(200, {"threads": chat_store.threads(ws), **chat_models.listing(config.load()), "limits": usage.latest()})
            if action == "log":
                return self._json(200, {"text": tail(ws.root / "job.log", 300)})
            if action == "export":
                if app.location.status != "idle":
                    raise app.location._error()
                from ..export.build import build
                out = build(ws, mobile=parse_qs(url.query).get("mobile") == ["1"])
                return self._download(out.read_bytes(), out.name, "text/html; charset=utf-8")
            if action == "part" and len(parts) > 5 and parts[5] in ("paper", "discussion", "reader", "layout", "job", "item"):
                version = ws.versions()[parts[5]]
                data = ws.load(parts[5])
                if parts[5] == "job":
                    data = jobs_api.job_state(data)
                return self._json(200, {"data": data, "version": version})
        if path.startswith("/p/"):
            _, _, pid, rel = path.split("/", 3)
            ws = lib.ws(pid)
            if ws and rel == "source.html" and (ws.load("paper") or {}).get("meta", {}).get("source_type") == "web":
                return self._file(ws.root / "source.html", ctype="text/html; charset=utf-8")
            if ws and re.fullmatch(r"extract/page-[0-9]{3,6}\.chars\.json", rel):
                if parse_qs(url.query).get("selection") == ["2"]:
                    from ..pdf.selectable_text import page_file
                    return self._file(page_file(ws.root, ws.source_pdf, int(rel[13:-11])))
                return self._file(_safe(ws.root, rel), cache=True)
            if ws and app.location.status == "idle" and rel.startswith("pages/") and "w=" in url.query:  # 原页面板用的小一号图，第一次请求时生成
                try:
                    w = int(parse_qs(url.query)["w"][0])
                except (KeyError, IndexError, ValueError):
                    w = 1000
                variant = pdfwork.page_variant(ws.root, rel, w)
                return self._file(variant or _safe(ws.root, rel), cache=True)
            if ws and re.fullmatch(r"chat-media/[0-9a-f]{32}\.png", rel):
                return self._file(_safe(ws.root, rel), cache=True)
            if ws and (rel.split("/", 1)[0] in ("pages", "figures") or rel == "source.pdf"):
                source = ws.source_pdf if rel == "source.pdf" else _safe(ws.root, rel)
                return self._file(source if source and source.is_file() else None, cache=rel != "source.pdf",
                                  ctype="application/pdf" if rel == "source.pdf" else None)
        return self._json(404, {"error": "not found"})

    # ---------- POST ----------
    def do_POST(self):
        if public_routes.handle(self):
            return
        if not self._host_ok() or not access_routes.guard(self):
            return
        if self.headers.get("X-Token") != self.app.token:  # 挡住别的网页跨站写
            self.close_connection = True  # Never parse an unread sensitive body as another request.
            return self._json(403, {"error": "bad token"})
        try:
            with self.app.location.request("POST", self.path):
                self._post()
        except (ValueError, KeyError) as e:
            self._json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("请求出错 %s", self.path)
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_DELETE(self):
        if not public_routes.handle(self):
            self._json(405, {"error": "method not allowed"})

    do_OPTIONS = do_DELETE
    do_PUT = do_DELETE
    do_PATCH = do_DELETE

    def _presence(self):
        """页面开着就一直连着这条 WebSocket；断开就是页面关了（见 presence.py）。"""
        host = self.headers.get("Host") or ""
        if not network.check_origin(self.headers.get("Origin"), host):  # 别的网站不能来占着
            self.close_connection = True
            return self._json(403, {"error": "bad origin"})
        p = self.app.presence
        self.app.location.enter_page()
        try:
            wsock.accept(self)
            self.close_connection = True
            wsock.hold(self)
        finally:
            if p:
                p.leave()

    def _post(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        app, lib = self.app, self.app.lib

        if access_routes.post(self, path) or import_routes.post(self, path) or qa_cli_api.handle(self):
            return
        if research_api.matches(path):
            result = research_api.post(app, path, q, self._body())
            return self._json(200, result)
        if path in library_api.POST:
            return self._json(200, library_api.post(app, path, json.loads(self._body() or b"{}")))
        if path in jobs_api.POST:  # 一键停止翻译
            return self._json(200, jobs_api.post(app, path, json.loads(self._body() or b"{}")))
        if path in bulk_api.POST:  # 从 Zotero 迁移、导入文件夹
            return self._json(200, bulk_api.post(app, path, json.loads(self._body() or b"{}")))
        if path == "/api/update":  # 开关“自动检查新版本”
            config.save({"check_updates": bool(json.loads(self._body() or b"{}").get("enabled"))})
            return self._json(200, updates.check())
        if path == "/api/presence/keep":  # easyread serve 复用了关页会退出的服务：改成常驻
            if app.presence:
                app.presence.keep()
            return self._json(200, {"ok": True})
        if path == "/api/import":  # 请求体就是 PDF 文件
            data = self._body()
            if q.get("babeldoc") == "1" and sha256_bytes(data)[:12] in app.babeldoc.running:
                raise ValueError("这篇文献正在生成 PDF，不能重复上传")
            ws, fresh = lib.create_from_pdf(data, q.get("name", "paper.pdf"), keep_filename=q.get("keep_filename") == "1")
            return self._import_result(ws, fresh, q.get("translate", "1") == "1", q.get("scope"),
                                       read=q.get("read") == "1", model=q.get("model", ""), target=q.get("target", ""), translation_extra=q.get("translation_extra", ""))
        if path in ("/api/import-url", "/api/import-arxiv"):
            body = json.loads(self._body() or b"{}")
            ws, fresh = lib.import_reference(body.get("ref", ""), body.get("kind", "auto"))
            return self._import_result(ws, fresh, bool(body.get("translate", True)), body.get("scope"),
                                       read=bool(body.get("read")), model=str(body.get("model") or ""), target=str(body.get("target") or ""), translation_extra=body.get("translation_extra", ""))
        if path in settings_api.POST:  # 设置页：保存配置、模型名单、试一下、取模型列表
            return self._json(200, settings_api.POST[path](json.loads(self._body() or b"{}")))
        if path == "/api/prefs":
            return self._json(200, prefs.save(json.loads(self._body() or b"{}")))
        if path == "/api/trash":
            body = json.loads(self._body() or b"{}")
            return self._json(200, research_api.trash_post(lib.root, body))

        if path.startswith("/api/p/"):
            parts = path.split("/")
            ws = lib.ws(parts[3]) if len(parts) > 4 else None
            if not ws:
                return self._json(404, {"error": tr("没有这篇论文")})
            if parts[4] in ("citation-import", "citation-import-pdf"):
                if parts[4] == "citation-import":
                    body = json.loads(self._body() or b"{}")
                    data, name, meta = lib.fetch(body.get("ref", ""))
                    ref_id, fingerprint = str(body.get("ref_id", "")), str(body.get("fingerprint", ""))
                else:
                    data, name, meta = self._body(), q.get("name", "paper.pdf"), None
                    ref_id, fingerprint = q.get("ref_id", ""), q.get("fingerprint", "")
                imported, fresh = lib.create_from_pdf(data, name, meta)
                return self._import_result(imported, fresh, False, None, citation=(ws, ref_id, fingerprint))
            body = json.loads(self._body() or b"{}")
            if parts[4] == "babeldoc" and len(parts) == 5:
                return self._json(200, app.babeldoc.start(ws, model=str(body.get("model") or ""),
                                  output_dir=str(body.get("output_dir") or ""), regenerate=bool(body.get("regenerate"))))
            if parts[4] == "babeldoc" and len(parts) == 6 and parts[5] == "delete":
                return self._json(200, app.babeldoc.delete(ws))
            if parts[4] == "chat" and len(parts) == 5:
                return self._chat(ws, body)
            if parts[4] == "notehelp":
                return notehelp.handle(self, ws, body)
            res = paper_api.post(app, ws, parts, body)
            if res is not None:
                return self._json(200, res)
        return self._json(404, {"error": "not found"})


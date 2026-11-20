"""启动 HTTP 服务：默认监听本机，容器可显式配置地址；记下本机地址给命令行复用。"""
from __future__ import annotations

import errno
import os
import signal
import socket
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer

from .. import __version__
from ..app import config, proxy
from ..engines import detect
from ..library import reader_files
from ..app.log import log, setup as setup_log
from .presence import Presence
from . import activity
from .server import App, Handler
from .network import allowed_hosts
from ..library.store import now_iso, read_json, write_json_atomic


class _Server(ThreadingHTTPServer):
    # Windows 上 SO_REUSEADDR 会让两个进程同时占住 8765，浏览器随机连到其中一个（比如旧版本）
    allow_reuse_address = os.name != "nt"

    def __init__(self, address, handler):
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, handler)


def _bind(host: str, port: int, fallback: bool):
    try:
        return _Server((host, port), Handler)
    except OSError as e:
        if not fallback or e.errno != errno.EADDRINUSE:
            raise
        return _Server((host, 0), Handler)


def serve(port: int | None = None, open_browser: bool = False, path: str = "/", exit_on_close: bool = False,
          host: str | None = None):
    """exit_on_close：start.cmd / start.sh 启动时为真，页面都关了、后台任务也做完了就退出。"""
    setup_log(config.LOG_PATH)
    started = time.monotonic()
    cfg = config.load()
    proxy.apply(cfg.get("system_proxy"))
    log.info("startup config-ready +%.0fms", (time.monotonic() - started) * 1000)
    app = App(cfg)
    log.info("startup library-jobs-ready +%.0fms", (time.monotonic() - started) * 1000)
    Handler.app = app
    detect.warm(cfg)
    bind_host = host or os.environ.get("EASYREAD_HOST") or "127.0.0.1"
    requested_port = port if port is not None else os.environ.get("EASYREAD_PORT")
    port = cfg["port"] if requested_port is None else int(requested_port)
    Handler.allowed_hosts = allowed_hosts(bind_host, os.environ.get("EASYREAD_ALLOWED_HOSTS", ""))
    # Desktop launches may pick a free port; an explicitly configured or remote
    # listener must fail instead of silently breaking Docker's port mapping.
    httpd = _bind(bind_host, port, fallback=requested_port is None and bind_host in ("127.0.0.1", "localhost", "::1"))
    local_host = "127.0.0.1" if bind_host == "0.0.0.0" else "::1" if bind_host == "::" else bind_host
    authority = f"[{local_host}]" if ":" in local_host else local_host
    url = f"http://{authority}:{httpd.server_address[1]}"
    log.info("startup http-bound +%.0fms", (time.monotonic() - started) * 1000)
    app.public_api.base_url = app.public_api.base_url or url
    app.shutdown = httpd.shutdown
    app.location.marker.start()
    app.presence = Presence(lambda: activity.model_work(app) or reader_files.busy() or app.location.active > 0, httpd.shutdown, exit_on_close)
    if not config.temp_library():
        write_json_atomic(config.SERVER_INFO, {"url": url, "pid": os.getpid(), "started": now_iso()})
    log.info("EasyRead %s 已启动：%s  文献库：%s", __version__, url, app.lib.root)
    print(f"EasyRead 已启动：{url}  文献库：{app.lib.root}", flush=True)  # i18n-ok Electron 和 smoke_backend 用正则匹配这行
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url + path)).start()
    main_thread = threading.current_thread() is threading.main_thread()
    previous_term = signal.getsignal(signal.SIGTERM) if main_thread else None
    if main_thread:
        # shutdown() must run outside serve_forever's thread to avoid deadlock.
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if main_thread:
            signal.signal(signal.SIGTERM, previous_term)
        if getattr(app, "import_tasks", None) is not None:
            try:
                app.import_tasks.close()
            except Exception:
                log.exception("Could not close browser import queue")
        from . import workspaces_api
        workspaces_api.close(app)
        app.public_api.close()
        app.terminals.close()
        app.location.marker.close()
        httpd.server_close()
        if not config.temp_library() and (read_json(config.SERVER_INFO, {}) or {}).get("pid") == os.getpid():
            config.SERVER_INFO.unlink(missing_ok=True)  # 下次 easyread 命令不会去连一个已经关掉的服务

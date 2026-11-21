"""HTTP access gate and settings actions, separate from paper routes."""
import json
import ssl
from urllib.parse import quote, urlsplit

from ..app import config, i18n
from ..app.i18n import tr
from .access import Access
from . import terminal, activity, pwa

PUBLIC = {"/login", "/web/css/access.css", "/web/js/common/access.js", "/web/favicon.svg",
          "/web/i18n/en.json", "/web/i18n/zh-Hant.json"}  # Static UI dictionaries contain no user data.


def same_origin(handler):
    origin = handler.headers.get("Origin")
    host = handler.headers.get("Host", "")
    return origin in (f"http://{host}", f"https://{host}") and handler.headers.get("Sec-Fetch-Site") not in ("cross-site", "none")


def body(handler):
    if int(handler.headers.get("Content-Length", "0")) > 4096:
        raise ValueError("Request too large")
    value = json.loads(handler._body() or b"{}")
    if not isinstance(value, dict):
        raise ValueError("Expected object")
    return value


def set_cookie(handler, sid):
    handler.access_cookie = Access.cookie(sid, isinstance(handler.connection, ssl.SSLSocket))


def guard(handler):
    access = handler.app.access
    path = urlsplit(handler.path).path
    handler.access_cookie = None
    handler.access_sid = access.session(handler.headers)
    if handler.command in ("GET", "HEAD") and path == "/healthz":
        handler._json(200, {"ok": True})
        return False
    if handler.command in ("GET", "HEAD") and path in PUBLIC | pwa.PUBLIC:
        if path == "/login":
            handler._page(config.WEB / "access.html")
            return False
        return True
    if path == "/api/access/login" and handler.command == "POST":
        handler.close_connection = True
        if not same_origin(handler):
            handler._json(403, {"error": "bad origin"})
            return False
        try:
            sid, code = access.login(body(handler).get("code", ""), handler.client_address[0])
            if sid:
                set_cookie(handler, sid)
            handler._json(code, {"ok": bool(sid), "error": tr("尝试过于频繁，请一分钟后重试") if code == 429 else tr("访问授权码不正确") if not sid else ""})
        except ValueError as error:
            handler._json(400, {"error": str(error)})
        return False
    handler.access_sid, issued = access.admit(handler.headers)
    if not handler.access_sid:
        handler.close_connection = True
        if handler.command in ("GET", "HEAD") and not path.startswith(("/api/", "/terminal/", "/workspace-terminal/")):
            handler._redirect("/login?next=" + quote(handler.path, safe=""))
        else:
            handler._json(401, {"error": tr("请输入访问授权码"), "login_required": True})
        return False
    if issued:
        set_cookie(handler, handler.access_sid)
    return True


def get(handler, path):
    if path == "/api/access":
        handler._json(200, {"enabled": handler.app.access.enabled, "terminal": handler.app.terminals.enabled})
    elif path == "/api/terminal/status":
        handler._json(200, {"enabled": handler.app.terminals.enabled, **terminal.status()})
    elif path.startswith("/terminal/"):
        handler.app.terminals.relay(handler, handler.access_sid)
    else:
        return False
    return True


def post(handler, path):
    if path not in ("/api/access", "/api/access/logout", "/api/terminal/open", "/api/terminal/close"):
        return False
    if not same_origin(handler):
        handler.close_connection = True
        handler._json(403, {"error": "bad origin"})
        return True
    data = body(handler)
    app = handler.app
    if path == "/api/access":
        sid = app.access.update(data)
        set_cookie(handler, sid)
        handler._json(200, {"enabled": app.access.enabled})
    elif path == "/api/access/logout":
        from .workspaces_api import revoke
        revoke(app, handler.access_sid)
        app.terminals.close(handler.access_sid)
        app.access.logout(handler.access_sid)
        set_cookie(handler, "")
        handler._json(200, {"ok": True})
    elif path == "/api/terminal/open":
        if data.get("action", "shell").endswith(("-update", "-install")) and activity.model_work(app):
            raise ValueError(tr("请等待翻译、研究分析或 PDF 任务完成后再更新 CLI"))
        handler._json(200, app.terminals.open(handler.access_sid, data.get("action", "shell")))
    else:
        app.terminals.close(handler.access_sid, data.get("url"))
        handler._json(200, {"ok": True})
    return True

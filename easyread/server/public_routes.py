"""Explicit machine-client boundary. Never bypass the browser's Cookie/X-Token gate."""
import json
from urllib.parse import parse_qs, unquote, urlsplit

from ..app.log import log
from . import network
from .public_api import APIError
from .public_imports import MAX_UPLOAD

PREFIX = "/api/v1"


def matches(raw):
    path = urlsplit(raw).path
    return path == "/mcp" or path == PREFIX or path.startswith(PREFIX + "/")


def error(handler, exc):
    handler.close_connection = True
    handler.send_response(exc.status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    if exc.status == 401:
        handler.send_header("WWW-Authenticate", 'Bearer realm="EasyRead"')
    if exc.status == 429:
        handler.send_header("Retry-After", "60")
    data = json.dumps(exc.body(), ensure_ascii=False).encode()
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(data)


def body(handler, limit):
    sizes = handler.headers.get_all("Content-Length", [])
    if handler.headers.get("Transfer-Encoding") or len(sizes) != 1 or not sizes[0].isascii() or not sizes[0].isdigit():
        raise APIError("invalid_request", "A single valid Content-Length is required")
    size = int(sizes[0])
    if size > limit:
        raise APIError("payload_too_large", f"Request exceeds {limit} bytes", 413)
    handler.connection.settimeout(30)
    data = handler.rfile.read(size)
    if len(data) != size:
        raise APIError("invalid_request", "Incomplete request body")
    return data


def handle(handler):
    if not matches(handler.path):
        return False
    try:
        if not network.check_host(handler.headers, handler.allowed_hosts):
            raise APIError("bad_host", "Host is not allowed", 403)
        api = handler.app.public_api
        path = urlsplit(handler.path).path
        enabled = api.settings["mcp_enabled" if path == "/mcp" else "enabled"]
        if not enabled:
            raise APIError("disabled", "This endpoint is disabled", 404)
        if not network.check_origin(handler.headers.get("Origin"), handler.headers.get("Host", "")) or handler.headers.get("Sec-Fetch-Site") == "cross-site":
            raise APIError("bad_origin", "Cross-origin requests are not allowed", 403)
        auth = handler.headers.get_all("Authorization", [])
        code = ""
        if len(auth) == 1:
            scheme, _, credential = auth[0].partition(" ")
            if scheme.lower() == "bearer":
                code = credential
        result = handler.app.access.check_code(code, handler.client_address[0])
        if result != 200:
            raise APIError("rate_limited" if result == 429 else "unauthorized",
                           "Too many authorization attempts" if result == 429 else "A valid access code is required", result)
        if path == "/mcp":
            # Stateless JSON mode needs no long-lived SSE stream or session DELETE.
            if handler.command != "POST":
                raise APIError("method_not_allowed", "Use Streamable HTTP POST", 405)
            data = body(handler, 1024 * 1024)
            try:
                bridge = api.mcp
            except ImportError:
                raise APIError("mcp_unavailable", "Install EasyRead with the mcp extra: pip install 'easyread[mcp]'", 503) from None
            bridge.dispatch(handler, data)
        else:
            _rest(handler, api, unquote(path[len(PREFIX):]))
    except APIError as exc:
        error(handler, exc)
    except (BrokenPipeError, ConnectionResetError):
        handler.close_connection = True
    except (ValueError, TypeError, TimeoutError) as exc:
        error(handler, APIError("invalid_request", "Malformed request or request timeout"))
    except Exception:
        log.exception("Public API request failed")
        error(handler, APIError("internal_error", "Internal server error", 500))
    return True


def _rest(handler, api, path):
    query = parse_qs(urlsplit(handler.path).query, keep_blank_values=True, max_num_fields=20)
    if any(len(v) != 1 for v in query.values()):
        raise APIError("invalid_parameter", "Duplicate query parameters are not allowed")
    q = {k: v[0] for k, v in query.items()}
    def params(*allowed):
        if set(q) - set(allowed):
            raise APIError("invalid_parameter", "Unknown query parameter")
        return q
    if handler.command in ("GET", "HEAD"):
        if path == "/info":
            params()
            result = api.info()
        elif path == "/openapi.json":
            from .public_schema import document
            params()
            result = document(api.base_url, api.app.access.enabled)
        elif path == "/documents":
            result = api.documents(**params("q", "category", "status", "limit", "offset"))
        elif path == "/categories":
            result = api.categories(**params("limit", "offset"))
        elif path.startswith("/tasks/") and len(path.split("/")) == 3:
            params()
            result = api.task(path.split("/")[2])
        elif path.startswith("/documents/"):
            parts = path.split("/")
            pid = parts[2]
            if len(parts) == 3:
                params()
                result = api.document(pid)
            elif len(parts) == 4 and parts[3] in ("notes", "highlights", "content", "links"):
                action = parts[3]
                allowed = ("block",) if action == "links" else ("limit", "offset", "language", "view") if action == "content" else ("limit", "offset")
                result = getattr(api, action)(pid, **params(*allowed))
            else:
                raise APIError("not_found", "Endpoint not found", 404)
        else:
            raise APIError("not_found", "Endpoint not found", 404)
        handler._json(200, result)
        return
    if handler.command == "POST" and path in ("/imports/file", "/imports/url", "/imports/html"):
        ctype = handler.headers.get_content_type()
        if path.endswith("/file"):
            params("filename", "translate", "target")
            if ctype != "application/pdf":
                raise APIError("unsupported_media_type", "Send PDF bytes with Content-Type: application/pdf", 415)
            if q.get("translate", "false") not in ("true", "false"):
                raise APIError("invalid_parameter", "translate must be true or false")
            result = api.imports.submit(data=body(handler, MAX_UPLOAD), filename=q.get("filename", "paper.pdf"),
                                        translate=q.get("translate") == "true", target=q.get("target", ""))
        else:
            params()
            if ctype != "application/json":
                raise APIError("unsupported_media_type", "Expected application/json", 415)
            is_html = path.endswith("/html")
            value = json.loads(body(handler, 12 * 1024 * 1024 if is_html else 16 * 1024))
            if is_html:
                if not isinstance(value, dict) or "html" not in value or set(value) - {"html", "title", "source_id", "translate", "target"}:
                    raise APIError("invalid_parameter", "Expected html and optional title, source_id, translate, target")
                result = api.imports.submit(**value)
            else:
                if not isinstance(value, dict) or "url" not in value or set(value) - {"url", "translate", "target", "kind"}:
                    raise APIError("invalid_parameter", "Expected url and optional translate, target, kind")
                result = api.import_url(**value)
        handler._json(202, result)
        return
    raise APIError("method_not_allowed", "Unsupported method or endpoint", 405)

"""Authenticated browser import task endpoints; legacy imports remain synchronous."""
import json

from ..app.i18n import tr
from .import_tasks import ImportTaskError, manager
from . import pwa

PREFIX = "/api/import-tasks"


def get(handler, path):
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        return False
    try:
        imports = manager(handler.app)
        if path == PREFIX:
            handler._json(200, {"scope_id": imports.scope, "library_id": pwa.library_id(handler.app.lib), "tasks": imports.list()})
        else:
            identity = path[len(PREFIX) + 1:]
            handler._json(200, {"task": imports.get(identity)})
    except ImportTaskError as error:
        handler._json(error.status, {"error": str(error)})
    return True


def post(handler, path):
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        return False
    try:
        size = int(handler.headers.get("Content-Length", "0"))
        if not 0 <= size <= 65536:
            handler.close_connection = True
            raise ImportTaskError(tr("导入请求过大"), 413)
        body = json.loads(handler._body() or b"{}")
        if not isinstance(body, dict):
            raise ImportTaskError(tr("导入参数必须是对象"))
        imports = manager(handler.app)
        if path == PREFIX:
            handler._json(202, {"task": imports.submit(body)})
        else:
            parts = path[len(PREFIX) + 1:].split("/")
            if len(parts) != 2 or parts[1] not in ("cancel", "ack", "retry"):
                raise ImportTaskError(tr("找不到导入任务"), 404)
            task = getattr(imports, parts[1])(parts[0])
            handler._json(202 if parts[1] == "retry" else 200, {"task": task})
    except ImportTaskError as error:
        handler._json(error.status, {"error": str(error)})
    return True

"""Transport-independent, read-only library facade shared by REST and MCP."""
from contextlib import contextmanager
from copy import deepcopy
import os
import re
import threading
from urllib.parse import quote, urlencode, urlsplit

from .. import __version__
from ..app import prefs
from ..translate import langs


class APIError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status

    def body(self):
        return {"error": {"code": self.code, "message": str(self)}}


def settings(cfg):
    saved = cfg.get("open_api") or {}
    def flag(env, key):
        value = os.environ.get(env)
        if value is None:
            return saved.get(key) is True
        if value not in ("0", "1"):
            raise ValueError(f"{env} must be 0 or 1")
        return value == "1"
    base = os.environ.get("EASYREAD_PUBLIC_URL", saved.get("public_url", "")).rstrip("/")
    if base:
        p = urlsplit(base)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise ValueError("Invalid EASYREAD_PUBLIC_URL")
    return {"enabled": flag("EASYREAD_OPEN_API", "enabled"),
            "mcp_enabled": flag("EASYREAD_MCP", "mcp_enabled"), "public_url": base}


def integer(value, name, default, low, high):
    if value is None:
        return default
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise APIError("invalid_parameter", f"{name} must be an integer")
    n = int(value)
    if not low <= n <= high:
        raise APIError("invalid_parameter", f"{name} must be between {low} and {high}")
    return n


def page(items, limit=50, offset=0):
    limit = integer(limit, "limit", 50, 1, 200)
    offset = integer(offset, "offset", 0, 0, 10_000_000)
    return {"items": items[offset:offset + limit], "total": len(items), "limit": limit,
            "offset": offset, "next_offset": offset + limit if offset + limit < len(items) else None}


class PublicAPI:
    def __init__(self, app, cfg):
        self.app = app
        self.settings = settings(cfg)
        self.base_url = self.settings["public_url"]
        self._imports = None
        self._mcp = None
        self._imports_lock = threading.Lock()
        self._mcp_lock = threading.Lock()
        self._mcp_error = None

    def close(self):
        if self._imports:
            self._imports.close()
        if self._mcp:
            self._mcp.close()

    @property
    def imports(self):
        with self._imports_lock:
            if self._imports is None:
                from .public_imports import Imports
                self._imports = Imports(self)
            return self._imports

    @property
    def mcp(self):
        with self._mcp_lock:
            if self._mcp_error:
                raise self._mcp_error
            if self._mcp is None:
                try:
                    from .public_mcp import MCPBridge
                    self._mcp = MCPBridge(self)
                except Exception as exc:
                    self._mcp_error = exc
                    raise
            return self._mcp

    @contextmanager
    def reading(self):
        # Prevent a concurrent move while reading, without marking a paper opened
        # or invoking repair/figure generation/AI/metadata refresh.
        try:
            with self.app.location.request("GET", "/api/p/public/part/paper"):
                yield
        except APIError:
            raise
        except ValueError as exc:
            raise APIError("library_unavailable", str(exc), 409) from None

    def workspace(self, document_id):
        if not isinstance(document_id, str):
            raise APIError("invalid_parameter", "document_id must be a string")
        ws = self.app.lib.ws(document_id)
        if not ws:
            raise APIError("not_found", "Document not found", 404)
        return ws

    def links(self, document_id: str, block: str | None = None):
        self.workspace(document_id)
        if block and (not isinstance(block, str) or len(block) > 200 or any(ord(c) < 32 for c in block)):
            raise APIError("invalid_parameter", "Invalid block ID")
        url = f"{self.base_url}/read/{quote(document_id, safe='')}"
        if block:
            url += "#" + quote(block, safe="")
        query = {"id": document_id}
        if block:
            query["block"] = block
        return {"web_url": url, "desktop_url": "easyread://open?" + urlencode(query)}

    def info(self):
        return {"api_version": "1", "version": __version__,
                "authentication_required": self.app.access.enabled, **self.settings,
                "capabilities": ["documents", "notes", "highlights", "content", "categories", "imports", "tasks", "import_html"],
                "max_upload_bytes": 50 * 1024 * 1024, "max_html_bytes": 8 * 1024 * 1024, "max_page_size": 200}

    def _summary(self, ws):
        result = self.app.lib.summary(ws, repair=False)
        result["links"] = self.links(ws.id)
        return result

    def documents(self, q: str = "", category: str = "", status: str = "", limit: int = 50, offset: int = 0):
        with self.reading():
            items = [self._summary(ws) for ws in self.app.lib.all()]
            if q:
                q = q.casefold()
                items = [x for x in items if q in " ".join(str(x.get(k, "")) for k in
                         ("display_title", "title_en", "title_zh", "authors", "abstract", "doi", "arxiv", "tags")).casefold()]
            if category:
                items = [x for x in items if any(t == category or t.startswith(category + "/") for t in x["tags"])]
            if status:
                items = [x for x in items if x["status"] == status]
            items.sort(key=lambda x: (x.get("added", ""), x["id"]), reverse=True)
            return page(items, limit, offset)

    def document(self, document_id: str):
        with self.reading():
            return self._summary(self.workspace(document_id))

    def notes(self, document_id: str, limit: int = 50, offset: int = 0):
        return self._annotations(document_id, False, limit, offset)

    def highlights(self, document_id: str, limit: int = 50, offset: int = 0):
        return self._annotations(document_id, True, limit, offset)

    def _annotations(self, document_id, highlights, limit, offset):
        with self.reading():
            ws = self.workspace(document_id)
            version = ws.versions()["reader"]
            reader = ws.load("reader") or {}
            items = []
            for key, note in reader.get("notes", {}).items():
                if note.get("deleted") or (note.get("kind") == "highlight") != highlights:
                    continue
                items.append({**note, "id": note.get("id", key),
                              "links": self.links(document_id, note.get("anchor"))})
            items.sort(key=lambda x: (x.get("created", ""), x["id"]))
            result = {**page(items, limit, offset), "document_id": document_id, "version": version}
            if not highlights:
                result["paper_note"] = reader.get("paper_note") or {}
            return result

    def content(self, document_id: str, language: str = "both", view: str = "effective", limit: int = 50, offset: int = 0):
        if language not in ("original", "translation", "both") or view not in ("effective", "raw"):
            raise APIError("invalid_parameter", "Invalid language or view")
        with self.reading():
            ws = self.workspace(document_id)
            versions = ws.versions()
            paper, reader = ws.load("paper") or {}, ws.load("reader") or {}
            edits = reader.get("edits") or {}
            selected = page(paper.get("blocks", []), limit, offset)
            blocks = []
            for raw in selected["items"]:
                b = deepcopy(raw)
                bid = b["id"]
                revisions = {k: v for k, v in edits.items() if k == bid or k.startswith(bid + "#")}
                if view == "effective":
                    for key, field in ((bid, "zh"), (bid + "#caption", "caption_zh"), (bid + "#image", "image_zh")):
                        if "zh" in revisions.get(key, {}):
                            b[field] = revisions[key]["zh"]
                    for i, item in enumerate(b.get("items", [])):
                        if "zh" in revisions.get(f"{bid}#{i}", {}):
                            item["zh"] = revisions[f"{bid}#{i}"]["zh"]
                if language != "both":
                    remove = ("zh", "caption_zh", "image_zh") if language == "original" else ("en", "caption_en", "image_en")
                    for key in remove:
                        b.pop(key, None)
                    for item in b.get("items", []):
                        item.pop("zh" if language == "original" else "en", None)
                blocks.append({**b, "has_user_edits": bool(revisions), "links": self.links(document_id, bid)})
            return {**selected, "items": blocks, "document_id": document_id, "language": language,
                    "view": view, "target": langs.of_paper(paper.get("meta")),
                    "versions": {k: versions[k] for k in ("paper", "reader")},
                    "translation": paper.get("translation", {})}

    def categories(self, limit: int = 50, offset: int = 0):
        with self.reading():
            paths = list((prefs.load().get("library") or {}).get("cats") or [])
            tags = [(ws.load("item") or {}).get("tags", []) for ws in self.app.lib.all()]
            paths.extend(t for group in tags for t in group)
            ordered = dict.fromkeys("/".join(p.split("/")[:i]) for p in paths
                                    if isinstance(p, str) and p for i in range(1, len(p.split("/")) + 1))
            def key(p):
                return tuple(list(ordered).index("/".join(p.split("/")[:i])) for i in range(1, len(p.split("/")) + 1))
            items = [{"path": p, "name": p.rsplit("/", 1)[-1], "parent": p.rpartition("/")[0] or None,
                      "count": sum(any(t == p or t.startswith(p + "/") for t in group) for group in tags)}
                     for p in sorted(ordered, key=key)]
            return page(items, limit, offset)

    def task(self, task_id: str):
        with self.reading():
            return self.imports.get(task_id)

    def import_url(self, url: str, translate: bool = False, target: str = "", kind: str = "auto"):
        return self.imports.submit(url=url, translate=translate, target=target, kind=kind)

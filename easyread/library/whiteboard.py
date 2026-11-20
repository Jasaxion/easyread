"""Opt-in, revision-checked Excalidraw documents, separate from reader operations."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import uuid
from urllib.parse import urlsplit

from .store import dir_lock, now_iso, read_json, write_json_atomic

MAX_BYTES = 32 * 1024 * 1024
MAX_ELEMENTS = 20000
MAX_PREVIEW_BYTES = 4 * 1024 * 1024
REVISION = re.compile(r"^[0-9a-f]{32}$")
APP_KEYS = frozenset((
    "viewBackgroundColor", "gridSize", "gridStep", "gridModeEnabled", "scrollX", "scrollY", "zoom",
    "exportBackground", "exportEmbedScene", "exportWithDarkMode", "exportScale", "name",
    "currentItemStrokeColor", "currentItemBackgroundColor", "currentItemFillStyle", "currentItemStrokeWidth",
    "currentItemStrokeStyle", "currentItemRoughness", "currentItemOpacity", "currentItemFontFamily",
    "currentItemFontSize", "currentItemTextAlign", "currentItemStartArrowhead", "currentItemEndArrowhead",
    "currentItemRoundness",
))


def safe_link(link):
    if not isinstance(link, str) or len(link) > 4096 or re.search(r"[\x00-\x20\\]", link):
        return False
    if link.startswith("#b-"):
        return True
    if link.startswith("/read/"):
        return True
    parsed = urlsplit(link)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and not parsed.username


def _elements(value):
    if not isinstance(value, list) or len(value) > MAX_ELEMENTS:
        raise ValueError("Invalid whiteboard elements")
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("type"), str):
            raise ValueError("Invalid whiteboard element")
        if item.get("link") and not safe_link(item["link"]):
            raise ValueError("Unsafe whiteboard link")
        if item.get("type") in ("iframe", "embeddable"):
            raise ValueError("Web embeds are not enabled in EasyRead whiteboards")
    return value


def validate(scene):
    if not isinstance(scene, dict):
        raise ValueError("Invalid whiteboard scene")
    # Reject NaN/Infinity and bound total payload, including embedded image data.
    try:
        encoded = json.dumps(scene, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("Invalid whiteboard JSON") from error
    if len(encoded) > MAX_BYTES:
        raise ValueError("Whiteboard exceeds 32 MiB; export a backup and reduce images")
    state, files, library = scene.get("appState", {}), scene.get("files", {}), scene.get("libraryItems", [])
    if not isinstance(state, dict) or not isinstance(files, dict) or not isinstance(library, list) or len(library) > 2000:
        raise ValueError("Invalid whiteboard state, files or library")
    for key, file in files.items():
        if not isinstance(file, dict) or file.get("id") != key or not isinstance(file.get("dataURL"), str):
            raise ValueError("Invalid whiteboard image")
        if not re.match(r"^data:image/(png|jpeg|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=\s]*$", file["dataURL"]):
            raise ValueError("Whiteboard images must be embedded image data")
    for item in library:
        if not isinstance(item, dict):
            raise ValueError("Invalid whiteboard library item")
        _elements(item.get("elements"))
    return {"type": "excalidraw", "version": 2, "source": "EasyRead",
            "elements": _elements(scene.get("elements")),
            "appState": {k: v for k, v in state.items() if k in APP_KEYS}, "files": files, "libraryItems": library}


def load(ws):
    value = read_json(ws.root / "whiteboard.json")
    if value is not None:
        if not isinstance(value, dict) or value.get("schema") != 1 or not isinstance(value.get("revision"), str) or not REVISION.fullmatch(value["revision"]):
            raise ValueError("Cannot read whiteboard; the original file has been preserved")
        validate(value.get("scene"))
    return value


def _previews(ws):
    return [p for p in ws.root.glob("whiteboard-preview-*.png")
            if REVISION.fullmatch(p.name[len("whiteboard-preview-"):-4])]


def preview_info(ws, board, url):
    revision = board["revision"] if board else None
    ready = bool(board and (ws.root / f"whiteboard-preview-{revision}.png").is_file())
    return {"status": "ready" if ready else "pending" if board else "absent", "revision": revision,
            "url": url + "?revision=" + revision if ready else None}


def preview(ws, revision=None):
    board = load(ws)
    if not board:
        return 404, {"error": "whiteboard_not_found"}
    if revision is not None and revision != board["revision"]:
        return 409, {"error": "whiteboard_conflict"}
    try:
        return 200, (ws.root / f"whiteboard-preview-{board['revision']}.png").read_bytes()
    except FileNotFoundError:
        return 409, {"error": "whiteboard_preview_pending"}


def _png(body):
    from PIL import Image
    data = body.get("image")
    if not isinstance(data, str) or len(data) > (MAX_PREVIEW_BYTES + 2) // 3 * 4:
        raise ValueError("Invalid whiteboard preview size")
    try:
        raw = base64.b64decode(data, validate=True)
        if len(raw) > MAX_PREVIEW_BYTES:
            raise ValueError("Whiteboard preview exceeds 4 MiB")
        if raw[:8] != b"\x89PNG\r\n\x1a\n" or not (0 < int.from_bytes(raw[16:20], "big") <= 1600 and 0 < int.from_bytes(raw[20:24], "big") <= 1600):
            raise ValueError("Expected a PNG preview up to 1600 by 1600 pixels")
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG" or not (0 < image.width <= 1600 and 0 < image.height <= 1600):
                raise ValueError("Expected a PNG preview up to 1600 by 1600 pixels")
            image.verify()
    except (OSError, SyntaxError) as error:
        raise ValueError("Invalid whiteboard PNG preview") from error
    return raw


def update(ws, body):
    if not isinstance(body, dict) or body.get("action") not in ("create", "save", "delete", "preview"):
        raise ValueError("Expected whiteboard create, save, delete or preview")
    if body["action"] in ("delete", "preview") and not isinstance(body.get("revision"), str):
        raise ValueError("Whiteboard revision is required")
    png = _png(body) if body["action"] == "preview" else None
    scene = None
    if body["action"] == "save":
        if not isinstance(body.get("revision"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", str(body.get("mutation", ""))):
            raise ValueError("Whiteboard revision and mutation are required")
        scene = validate(body.get("scene"))
    with dir_lock(ws.root):
        existing = load(ws)
        if body["action"] == "delete":
            if existing and body["revision"] != existing["revision"]:
                return 409, {"error": "whiteboard_conflict"}
            for path in _previews(ws):
                path.unlink(missing_ok=True)
            (ws.root / "whiteboard.json").unlink(missing_ok=True)
            return 200, {"board": None}
        if body["action"] == "preview":
            if not existing or body["revision"] != existing["revision"]:
                return 409, {"error": "whiteboard_conflict"}
            path = ws.root / f"whiteboard-preview-{existing['revision']}.png"
            temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("wb") as stream:
                    stream.write(png)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            for old in _previews(ws):
                if old != path:
                    old.unlink(missing_ok=True)
            return 200, {"revision": existing["revision"]}
        if body["action"] == "create":
            if existing is not None:
                return 200, {"board": existing}
            scene = validate({"elements": [], "appState": {"viewBackgroundColor": "#f6f3ec"}, "files": {}})
        else:
            digest = hashlib.sha256(json.dumps(scene, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if existing and existing.get("mutation") == body["mutation"] and existing.get("digest") == digest:
                return 200, {"revision": existing["revision"]}
            if existing is None or body["revision"] != existing["revision"]:
                return 409, {"error": "whiteboard_conflict"}
        board = {"schema": 1, "revision": uuid.uuid4().hex, "updated": now_iso(), "scene": scene}
        if body["action"] == "save":
            board.update(mutation=body["mutation"], digest=digest)
        write_json_atomic(ws.root / "whiteboard.json", board)
    return 200, {"board": board} if body["action"] == "create" else {"revision": board["revision"]}

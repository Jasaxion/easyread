"""Opt-in, revision-checked Excalidraw documents, separate from reader operations."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from urllib.parse import urlsplit

from .store import dir_lock, now_iso, read_json, write_json_atomic

MAX_BYTES = 32 * 1024 * 1024
MAX_ELEMENTS = 20000
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
        if not isinstance(value, dict) or value.get("schema") != 1 or not isinstance(value.get("revision"), str):
            raise ValueError("Cannot read whiteboard; the original file has been preserved")
        validate(value.get("scene"))
    return value


def update(ws, body):
    if not isinstance(body, dict) or body.get("action") not in ("create", "save"):
        raise ValueError("Expected whiteboard create or save")
    scene = None
    if body["action"] == "save":
        if not isinstance(body.get("revision"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", str(body.get("mutation", ""))):
            raise ValueError("Whiteboard revision and mutation are required")
        scene = validate(body.get("scene"))
    with dir_lock(ws.root):
        existing = load(ws)
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

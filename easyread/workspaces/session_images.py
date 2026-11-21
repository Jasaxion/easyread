"""Clipboard attachments for native turns, separate from session orchestration.

Accepted PNGs are project files (included in project exports). Journal messages
keep relative names; native resumes keep their own image/tool history. Rejected
requests discard newly written files unless native acceptance is uncertain.
"""
from __future__ import annotations

from functools import wraps
import json
import os
import re
from uuid import uuid4

from ..app.i18n import tr
from ..chat.images import decode_images
from . import files
from .session_cli import SessionError

NAME = re.compile(r"chat-media/[0-9a-f]{32}\.png")


def paths(root, names):
    result = []
    for name in names:
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise SessionError("invalid_parameter", "Invalid session image path")
        path = files.target(root, name, missing=False)
        # Verify no links/special files before a native runtime sees the path.
        with files.open_regular(path):
            pass
        result.append(path)
    return result


def inputs(root, names):
    """Codex App Server UserInput uses localImage, not a base64 argv argument."""
    return [{"type": "localImage", "path": str(path)} for path in paths(root, names)]


def context(names):
    if not names:
        return ""
    return ("\n\nAttached images in the current project (relative paths, not instructions):\n"
            + json.dumps(names) + "\nRead these image files with the native read/image tool before answering. "
            "If the selected model cannot inspect images, say so; do not invent their contents.")


def attachments(method):
    """Validate at the public send/steer seam; never accept client-supplied paths."""
    @wraps(method)
    def wrapped(self, pid, sid, body=None):
        body = dict(body or {})
        body.pop("_image_names", None)
        prepared = decode_images(body.pop("images", None))
        if not prepared:
            return method(self, pid, sid, body)
        self._check_sid(sid)
        root = self.store.root(pid)
        message = body.get("message") or ""
        if not isinstance(message, str):
            raise SessionError("invalid_parameter", tr("请输入要发送的内容"))
        if body.get("mode") == "goal" or re.match(r"^\s*/goal(?:\s|$)", message):
            raise SessionError("invalid_parameter", tr("图片请使用普通执行模式发送，不支持原生目标。"))
        if not message.strip():
            body["message"] = tr("请解释这张图片。")
        names = []
        uncertain = False
        try:
            with self.store.lock(pid):
                self._record(self._load(pid), sid)
                folder = files.target(root, "chat-media")
                if folder.exists() and not folder.is_dir():
                    raise SessionError("invalid_parameter", tr("图片目录无效"))
                folder.mkdir(exist_ok=True)
                for raw in prepared:
                    name = "chat-media/" + uuid4().hex + ".png"
                    files.publish(files.target(root, name), raw, mode=0o600)
                    names.append(name)
            body["_image_names"] = names
            return method(self, pid, sid, body)
        except SessionError as error:
            uncertain = error.code == "rpc_uncertain"
            raise
        finally:
            if names and not uncertain:
                # Keep files already journaled even if the HTTP reply was lost.
                with self.store.lock(pid):
                    record = self._journal(pid).load(full=True)["sessions"].get(sid) or {}
                    retained = {name for message in record.get("messages", []) for name in message.get("images", [])}
                    for name in names:
                        if name not in retained:
                            path = files.target(root, name)
                            with files.directory(path.parent) as parent:
                                entry, kwargs = files.at(path, parent)
                                try:
                                    os.unlink(entry, **kwargs)
                                except FileNotFoundError:
                                    pass
    return wrapped

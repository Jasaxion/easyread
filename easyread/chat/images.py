"""Shared clipboard image validation; storage never trusts client filenames."""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from uuid import uuid4

from ..app.i18n import tr
from ..engines import engines
from ..library.store import Workspace

HISTORY = 12
# 20 MiB decoded images plus base64 expansion and bounded JSON/text overhead.
MAX_REQUEST = 30 * 1024 * 1024


def decode_images(images) -> list[bytes]:
    if images is None:
        return []
    if not isinstance(images, list) or len(images) > 4:
        raise ValueError(tr("每次最多粘贴 4 张图片"))
    from PIL import Image
    prepared, total = [], 0
    for data in images:
        if not isinstance(data, str) or len(data) > 12 * 1024 * 1024:
            raise ValueError(tr("图片过大，单张最多 8 MB"))
        match = re.fullmatch(r"data:image/(?:png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", data)
        if not match:
            raise ValueError(tr("仅支持 PNG、JPEG 或 WebP 图片"))
        try:
            raw = base64.b64decode(match[1], validate=True)
        except ValueError as error:
            raise ValueError(tr("图片无法读取，请重新复制后粘贴")) from error
        total += len(raw)
        if len(raw) > 8 * 1024 * 1024 or total > 20 * 1024 * 1024:
            raise ValueError(tr("单张图片最多 8 MB，合计最多 20 MB"))
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ("PNG", "JPEG", "WEBP") or image.width * image.height > 16000000:
                    raise ValueError(tr("图片格式不支持或尺寸过大"))
                image.load()
                out = io.BytesIO()
                image.convert("RGBA" if "A" in image.getbands() else "RGB").save(out, "PNG")
                if out.tell() > 8 * 1024 * 1024 or sum(map(len, prepared)) + out.tell() > 20 * 1024 * 1024:
                    raise ValueError(tr("图片转存后过大，请缩小图片后重新粘贴"))
                prepared.append(out.getvalue())
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError(tr("图片无法读取，请重新复制后粘贴")) from error
    return prepared


def receive_images(ws: Workspace, images, ecfg: dict) -> list[str]:
    """Validate clipboard images before persisting PNGs under this paper only."""
    if images is None:
        return []
    if not isinstance(images, list) or len(images) > 4:
        raise ValueError(tr("每次最多粘贴 4 张图片"))
    if images and ecfg.get("engine") != "openai" and engines.image_mode(ecfg) == "text":
        raise ValueError(tr("当前模型未启用看图，请选择支持图片的模型"))
    prepared = decode_images(images)
    names = []
    if prepared:
        folder = ws.root / "chat-media"
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()) or not folder.resolve().is_relative_to(ws.root.resolve()):
            raise ValueError(tr("图片目录无效"))
        folder.mkdir(exist_ok=True)
        for raw in prepared:
            name = "chat-media/" + uuid4().hex + ".png"
            with (ws.root / name).open("xb") as file:
                file.write(raw)
            names.append(name)
    return names


def image_paths(ws: Workspace, messages: list[dict]) -> list[Path]:
    paths = []
    for message in messages[-HISTORY:]:
        for name in message.get("images", []) if isinstance(message.get("images", []), list) else []:
            if isinstance(name, str) and re.fullmatch(r"chat-media/[0-9a-f]{32}\.png", name):
                path = (ws.root / name).resolve()
                if path.is_relative_to(ws.root.resolve()) and path.is_file() and path not in paths:
                    paths.append(path)
    return paths[-4:]

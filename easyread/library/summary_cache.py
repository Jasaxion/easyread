"""Bounded, versioned list summaries; direct/read-only summaries stay uncached."""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from ..app.i18n import tr
from .store import DataReadError, Workspace


@dataclass
class _Entry:
    version: tuple
    summary: dict
    size: int


def summary_version(ws: Workspace) -> tuple:
    # Do not import the image renderer just to stat its well-known output path.
    paths = [ws.root / name for name in (
        "paper.json", "item.json", "reader.json", "discussion.json", "job.json",
        "source.pdf", "source.html", "pages/page-001.webp", "pages/web-cover.webp",
    )]
    versions = []
    for path in paths:
        try:
            st = path.stat()
            # As in passage_search, use file versions rather than reading JSON.
            # Identity/ctime also catch atomic replacements preserving mtime/size.
            versions.append((st.st_mtime_ns, st.st_size, st.st_dev, st.st_ino, st.st_ctime_ns))
        except FileNotFoundError:
            versions.append(None)
    return tuple(versions)


class SummaryCache:
    def __init__(self, *, max_entries: int = 512, max_bytes: int = 8 * 1024 * 1024):
        self._papers: dict[Path, _Entry] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._max_bytes = max_bytes

    def discard(self, root: Path) -> None:
        with self._lock:
            self._papers.pop(root, None)

    def snapshot(self, library) -> list[dict]:
        with self._lock:
            live, items, size = {}, [], 0
            for ws in library.all():
                # Capture before loading: repair/concurrent writes must trigger
                # a later refresh, never label old data with a newer version.
                version = summary_version(ws)
                entry = self._papers.get(ws.root)
                try:
                    cacheable = True
                    if entry is None or entry.version != version:
                        summary, cacheable = library._summary(ws)
                        entry = _Entry(version, summary, len(json.dumps(summary, ensure_ascii=False).encode("utf-8")))
                    items.append(deepcopy(entry.summary))
                    if cacheable and len(live) < self._max_entries and size + entry.size <= self._max_bytes:
                        live[ws.root] = entry
                        size += entry.size
                except DataReadError as e:
                    # Do not retain failures: retry and localize on each request.
                    items.append({"id": ws.id, "title_en": ws.id, "tags": [],
                                  "status": "unread", "load_error": tr(
                                      "无法读取数据文件：{path}；原文件已保留，请从备份恢复或联系维护者", path=str(e.path))})
            self._papers = live  # Prune deletions, including external removals.
            return items

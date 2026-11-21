"""Bounded Pi skill metadata discovery; never load packages or execute resources."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from . import native_cli_settings


def settings():
    try:
        return native_cli_settings.read("pi")
    except (OSError, ValueError):
        return {}


def roots():
    agent = native_cli_settings.path("pi").parent.absolute()
    paths = [agent / "skills", Path.home() / ".agents" / "skills"]
    configured = settings().get("skills")
    for entry in configured[:100] if isinstance(configured, list) else []:
        if not isinstance(entry, str) or not entry or entry.startswith(("-", "!")):
            continue
        entry = entry.removeprefix("+")
        if not entry or any(c in entry for c in "*?[]"):
            continue
        path = Path(entry).expanduser()
        paths.append(path if path.is_absolute() else agent / path)
    return list(dict.fromkeys(paths))


def enabled(item):
    """Honor explicit native path exclusions for the bounded user-level inventory."""
    agent = native_cli_settings.path("pi").parent.absolute()
    path = Path(item["path"])
    result = True
    configured = settings().get("skills")
    for entry in configured if isinstance(configured, list) else []:
        if not isinstance(entry, str) or not entry.startswith(("-", "!", "+")):
            continue
        pattern = Path(entry[1:]).expanduser()
        if not pattern.is_absolute():
            pattern = agent / pattern
        pattern = os.path.abspath(pattern)
        matches = any(fnmatch.fnmatchcase(str(candidate), pattern) for candidate in (path, *path.parents))
        if matches:
            result = entry[0] == "+"
    return result


def inventory(*, strict=False):
    from . import qa_skills
    items, seen = [], set()
    for root in roots():
        pending = [(root, 0)]
        visited = 0
        while pending and visited < qa_skills.MAX_ROOT_ENTRIES:
            directory, depth = pending.pop()
            visited += 1
            try:
                if directory.suffix.lower() == ".md":
                    candidates = [directory]
                else:
                    qa_skills._safe_chain(directory)
                    candidates = [directory / "SKILL.md"]
                    children = [child for child in qa_skills._children(directory, strict=strict)
                                if not child.name.startswith(".") and child.name != "SKILL.md"
                                and (child.is_dir() or child.suffix.lower() == ".md")]
                    if strict and (visited + len(pending) + len(children) > qa_skills.MAX_ROOT_ENTRIES
                                   or (depth >= 8 and children)):
                        raise ValueError("Pi skill root is too large to check for conflicts safely")
                    if depth < 8:
                        pending.extend((child, depth + 1) for child in reversed(children[:qa_skills.MAX_ROOT_ENTRIES - visited - len(pending)]))
                for path in candidates:
                    try:
                        metadata = qa_skills._read_metadata(path)
                    except (OSError, ValueError):
                        continue
                    canonical = path.resolve()
                    if canonical not in seen:
                        items.append(qa_skills._item(path, False, metadata))
                        seen.add(canonical)
            except (OSError, ValueError):
                if strict:
                    raise
                continue
    return items

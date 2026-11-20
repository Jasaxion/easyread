"""Validate a source ZIP completely, then publish with rollback; never run it."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

from . import WorkspaceError, files, latex


class _RecoveryRequired(WorkspaceError):
    """Do not remove displaced files when the filesystem also rejects rollback."""


def _path(name: str) -> str:
    files.relative(name)
    parts = name.split("/")
    if len(name) > 512 or len(parts) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise WorkspaceError("Invalid ZIP path")
    # Avoid aliases on Windows as well as literal traversal on POSIX.
    if any(p.endswith((".", " ")) or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in parts):
        raise WorkspaceError("Unsafe ZIP path")
    return name


def _entries(archive):
    infos = archive.infolist()
    if not infos or len(infos) > latex.MAX_FILES:
        raise WorkspaceError("ZIP must contain at most 10000 entries", 413)
    entries, seen, layout, total = [], set(), {}, 0
    for info in infos:
        if info.orig_filename != info.filename:
            raise WorkspaceError("Invalid ZIP filename")
        name = _path(info.filename[:-1] if info.is_dir() else info.filename)
        if name.split("/")[0] == "__MACOSX" or PurePosixPath(name).name == ".DS_Store":
            continue  # Finder packaging metadata must not prevent wrapper flattening.
        key = name.casefold()
        if key in seen:
            raise WorkspaceError("Duplicate ZIP path: " + name)
        seen.add(key)
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if info.flag_bits & 1 or kind not in (0, stat.S_IFDIR, stat.S_IFREG):
            raise WorkspaceError("ZIP encryption, links and special files are not supported")
        if kind == stat.S_IFDIR and not info.is_dir() or kind == stat.S_IFREG and info.is_dir():
            raise WorkspaceError("Invalid ZIP file type")
        # Old Unix ZIP extras can encode link targets even with a regular mode.
        extra = info.extra
        while extra:
            if len(extra) < 4:
                raise WorkspaceError("Invalid ZIP metadata")
            tag, size = int.from_bytes(extra[:2], "little"), int.from_bytes(extra[2:4], "little")
            if len(extra) < size + 4 or tag in (0x000D, 0x756E):
                raise WorkspaceError("ZIP link metadata is not supported")
            extra = extra[size + 4:]
        total += info.file_size
        if info.file_size < 0 or total > latex.MAX_SOURCE:
            raise WorkspaceError("ZIP expansion exceeds 128 MiB", 413)
        if info.is_dir() and info.file_size:
            raise WorkspaceError("ZIP directory contains data")
        for parent in reversed(PurePosixPath(name).parents):
            if parent.as_posix() != ".":
                parent_name = parent.as_posix()
                old = layout.get(parent_name.casefold())
                if old and old != (parent_name, True):
                    raise WorkspaceError("ZIP file/directory or case collision")
                layout[parent_name.casefold()] = (parent_name, True)
        old = layout.get(key)
        if old and old != (name, info.is_dir()):
            raise WorkspaceError("ZIP file/directory or case collision")
        layout[key] = (name, info.is_dir())
        if len(layout) > latex.MAX_FILES:
            raise WorkspaceError("Too many ZIP paths including implicit directories", 413)
        entries.append((info, name))
    # A single enclosing directory is packaging, not part of the TeX project.
    roots = {name.split("/")[0] for _, name in entries}
    wrapper = next(iter(roots)) if len(roots) == 1 and all(info.is_dir() or "/" in name for info, name in entries) else None
    result = []
    for info, name in entries:
        if wrapper:
            if name == wrapper:
                continue
            name = name[len(wrapper) + 1:]
        if name.split("/")[0].casefold() in (latex.BUILD_DIR.casefold(), latex.STATE_FILE.casefold()):
            raise WorkspaceError("ZIP must not contain compiler state or build outputs")
        result.append((info, name))
    return result


def _extract(archive, entries, destination):
    total = 0
    for info, name in entries:
        target = destination / name
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with archive.open(info) as src, target.open("xb") as dest:
            while chunk := src.read(65536):
                count += len(chunk)
                total += len(chunk)
                if total > latex.MAX_SOURCE or count > info.file_size:
                    raise WorkspaceError("ZIP expansion exceeds declared size or 128 MiB", 413)
                dest.write(chunk)
        if count != info.file_size:
            raise WorkspaceError("Truncated ZIP entry")
        # Uploaded executable bits, ownership and timestamps are never applied.
        target.chmod(0o644)


def _main(stage: Path, entries) -> str:
    candidates = sorted(name for info, name in entries if not info.is_dir() and name.lower().endswith(".tex"))
    if "main.tex" in candidates:
        return latex._main(stage, "main.tex")
    for name in candidates:
        text = files.read_bytes(stage / name, latex.MAX_SOURCE).decode("utf-8", errors="replace")
        text = re.sub(r"(?m)(?<!\\)%.*$", "", text)
        if re.search(r"\\documentclass\s*(?:\[[^\]]*\]\s*)?\{", text):
            return latex._main(stage, name)
    raise WorkspaceError("No main.tex or documentclass source found in ZIP")


def _conflicts(workspace: Path, entries, replace: bool):
    conflicts, types = [], []
    layout = {"latex-workspace": True}
    for info, name in entries:
        rel = "latex-workspace/" + name
        for parent in PurePosixPath(rel).parents:
            if parent.as_posix() != ".":
                layout[parent.as_posix()] = True
        layout[rel] = info.is_dir()
    for rel, is_directory in sorted(layout.items(), key=lambda item: (len(PurePosixPath(item[0]).parts), item[0])):
        if any(rel.startswith(conflict + "/") for conflict in types):
            continue
        path = files.target(workspace, rel)
        if not path.parent.exists():
            continue
        # Prevent differently cased aliases from overwriting on another platform.
        aliases = [p.name for p in path.parent.iterdir() if p.name.casefold() == path.name.casefold() and p.name != path.name]
        if aliases:
            types.append(rel)
        if path.exists():
            if path.is_dir() != is_directory:
                types.append(rel)
            elif not is_directory and not replace:
                conflicts.append(rel)
    if types or conflicts:
        paths = types or conflicts
        message = "File/directory or case conflicts (cannot replace): " if types else "Files already exist: "
        raise WorkspaceError(message + ", ".join(paths), 409, "type_conflict" if types else "file_conflict")


def _publish(workspace, stage, entries, state, transaction):
    created_dirs, published, backups = [], [], []
    root = workspace / "latex-workspace"
    backup = transaction / "backup"
    backup.mkdir()
    def mkdir(path):
        if path == workspace or path.exists():
            return
        mkdir(path.parent)
        with files.directory(path.parent) as parent:
            name, kwargs = files.at(path, parent)
            os.mkdir(name, 0o755, **kwargs)
        created_dirs.append(path)
    sources = [(name, stage / name) for info, name in entries if not info.is_dir()]
    settings = transaction / "settings.json"
    settings.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    sources.append((latex.STATE_FILE, settings))
    try:
        mkdir(root)
        for info, name in entries:
            if info.is_dir():
                mkdir(root / name)
        for name, source in sources:
            destination = files.target(workspace, "latex-workspace/" + name)
            mkdir(destination.parent)
            if destination.exists():
                saved = backup / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                files.relocate(destination, saved)
                backups.append((destination, saved))
            # Exclusive publication: a concurrent creator is never overwritten.
            files.publish(destination, files.read_bytes(source, latex.MAX_SOURCE), 0o600 if name == latex.STATE_FILE else 0o644)
            published.append((destination, source))
    except Exception:
        try:
            for path, source in reversed(published):
                if files.read_bytes(path, latex.MAX_SOURCE) != files.read_bytes(source, latex.MAX_SOURCE):
                    raise OSError("Concurrent file change prevents rollback")
                with files.directory(path.parent) as parent:
                    name, kwargs = files.at(path, parent)
                    os.unlink(name, **kwargs)
            for destination, saved in reversed(backups):
                if destination.exists() or destination.is_symlink():
                    raise OSError("Concurrent file creation prevents rollback")
                files.relocate(saved, destination)
            for path in reversed(created_dirs):
                path.rmdir()
        except (OSError, WorkspaceError):
            raise _RecoveryRequired("Import could not fully roll back; displaced files are preserved at " +
                                    str(backup), 409, "file_conflict") from None
        raise


def import_zip(workspace: Path, raw: bytes, *, replace=False) -> dict:
    """Caller holds the project lock and has checked AI/terminal idleness."""
    if len(raw) > files.MAX_FILE:
        raise WorkspaceError("ZIP exceeds 200 MiB upload limit", 413)
    with latex._LOCK:
        if latex.busy(workspace):
            raise WorkspaceError("Stop LaTeX compilation before importing", 409, "project_busy")
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = _entries(archive)
                # Keep rollback on the same filesystem, outside the source tree.
                transaction = Path(tempfile.mkdtemp(prefix=".easyread-latex-import-", dir=workspace.parent))
                retain = False
                try:
                    stage = transaction / "source"
                    stage.mkdir()
                    _extract(archive, entries, stage)
                    main = _main(stage, entries)
                    _conflicts(workspace, entries, replace)
                    state = latex._load(latex._root(workspace, create=False), create=False)
                    state.update(main=main, initialized=True, compilation=latex._idle())
                    _publish(workspace, stage, entries, state, transaction)
                except _RecoveryRequired:
                    retain = True
                    raise
                finally:
                    if not retain:
                        shutil.rmtree(transaction, ignore_errors=True)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
            raise WorkspaceError("Invalid ZIP or import failed: " + str(exc)) from None
    return {**state, "engines": latex.capabilities(),
            "imported": ["latex-workspace/" + name for info, name in entries if not info.is_dir()]}

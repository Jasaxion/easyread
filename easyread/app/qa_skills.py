"""Bounded, read-only native skill discovery and QA-only managed imports.

Native discovery lists immediate, non-symlink skill directories (at most 1,000
entries per root), not the CLI's complete discovery rules. Imports are inert;
callers decide whether/how to expose them in a per-call QA overlay. Invalid
uploads, unsupported engines and unresolved IDs raise ValueError.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import itertools
import os
import re
import shutil
import stat
import tempfile
import zipfile
import zlib
from pathlib import Path
from typing import TypedDict

from . import config

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_EXPANDED_BYTES = 12 * 1024 * 1024
MAX_FILES = 150
MAX_SKILL_BYTES = 128 * 1024
MAX_ROOT_ENTRIES = 1000
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_TEXT = {".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".csv",
         ".tsv", ".xml", ".html", ".css", ".tex", ".bib", ".sql", ".ipynb", ".svg"}
_SCRIPTS = {".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".r", ".ps1", ".lua"}
_BINARY = {".pdf": (b"%PDF-",), ".png": (b"\x89PNG\r\n\x1a\n",),
           ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
           ".gif": (b"GIF87a", b"GIF89a"), ".webp": (b"RIFF",)}
_FOLDERS = {"scripts", "references", "assets", "agents"}
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}


class SkillItem(TypedDict):
    id: str
    name: str
    description: str
    path: str
    managed: bool


def _engine(engine: str) -> None:
    if engine not in ("claude", "codex", "pi"):
        raise ValueError("Skills support only claude, codex and pi")


def _native_roots(engine: str) -> list[Path]:
    home = Path.home()
    if engine == "pi":
        from . import pi_skills
        return pi_skills.roots()
    if engine == "claude":
        return [Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser() / "skills"]
    codex = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser()
    return list(dict.fromkeys([home / ".agents" / "skills", codex.absolute() / "skills"]))


def _safe_chain(path: Path) -> None:
    """Reject linked/special ancestors without resolving away evidence of a link."""
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        # Windows junctions are reparse points but can report directory mode.
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
            raise ValueError("Skill directories must not contain symlinks or special files")


def _managed_root(engine: str) -> Path:
    root = Path(config.HOME).absolute() / "qa-skills" / engine
    canonical = root.resolve()
    for kind in ("claude", "codex", "pi"):
        for native in _native_roots(kind):
            native = native.resolve()
            if canonical == native or canonical.is_relative_to(native) or native.is_relative_to(canonical):
                raise ValueError("QA skills must be outside native skill roots")
    return root


def _children(root: Path, *, strict: bool = False) -> list[Path]:
    try:
        _safe_chain(root)
        with os.scandir(root) as entries:
            paths = [Path(e.path) for e in itertools.islice(entries, MAX_ROOT_ENTRIES + 1)]
        if strict and len(paths) > MAX_ROOT_ENTRIES:
            raise ValueError("Skill root is too large to check for conflicts safely")
        return sorted(paths[:MAX_ROOT_ENTRIES], key=lambda p: p.name)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        if strict:
            raise
        return []


def _text(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Skill text must be UTF-8") from exc
    if any((ord(c) < 32 and c not in "\t\r\n") or ord(c) == 127 for c in text):
        raise ValueError("Skill text contains binary control characters")
    return text


def _metadata(data: bytes) -> tuple[str, str]:
    if len(data) > MAX_SKILL_BYTES:
        raise ValueError("SKILL.md exceeds 128 KiB")
    lines = _text(data).splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("SKILL.md requires frontmatter name and description")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("SKILL.md has unclosed frontmatter") from exc
    values: dict[str, str] = {}
    i = 1
    while i < end:
        match = re.fullmatch(r"(name|description):\s*(.*)", lines[i])
        i += 1
        if not match:
            continue
        key, value = match.groups()
        if key in values:
            raise ValueError("Duplicate skill metadata field")
        if value in ("|", "|-", "|+", ">", ">-", ">+"):
            block = []
            while i < end and (not lines[i].strip() or lines[i].startswith((" ", "\t"))):
                block.append(lines[i].strip())
                i += 1
            value = (" " if value.startswith(">") else "\n").join(block).strip()
        elif value.startswith(("'", '"')):
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise ValueError("Invalid quoted skill metadata")
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
            if not value or value[0] in "[{&*!>|" or value.lower() in ("null", "~", "true", "false"):
                raise ValueError("Skill metadata must be nonempty text")
        if not value.strip():
            raise ValueError("Skill metadata must be nonempty text")
        values[key] = value
    name, description = values.get("name", ""), values.get("description", "")
    if not _NAME.fullmatch(name) or name in _RESERVED or not description:
        raise ValueError("SKILL.md requires a safe name and nonempty description")
    return name, description


def _read_metadata(path: Path) -> tuple[str, str]:
    _safe_chain(path.parent)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
        raise ValueError("SKILL.md must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SKILL_BYTES:
            raise ValueError("SKILL.md must be a bounded regular file")
        return _metadata(stream.read(MAX_SKILL_BYTES + 1))


def _item(path: Path, managed: bool, metadata: tuple[str, str]) -> SkillItem:
    canonical = path.resolve()
    name, description = metadata
    identifier = f"managed:{path.parent.name}" if managed else "native:" + hashlib.sha256(str(canonical).encode()).hexdigest()[:16]
    return {"id": identifier, "name": name, "description": description,
            "path": str(canonical), "managed": managed}


def inventory(engine: str) -> list[SkillItem]:
    """Return metadata only; never create roots or change native skill files."""
    _engine(engine)
    root = _managed_root(engine)
    items: list[SkillItem] = []
    seen: set[Path] = set()
    for managed, base in [(True, root), *((False, p) for p in (_native_roots(engine) if engine != "pi" else []))]:
        for directory in _children(base):
            if directory.name.startswith("."):
                continue
            path = directory / "SKILL.md"
            try:
                metadata = _read_metadata(path)
                if managed and (not _NAME.fullmatch(directory.name) or directory.name != metadata[0]):
                    continue
                canonical = path.resolve()
                if canonical not in seen:
                    items.append(_item(path, managed, metadata))
                    seen.add(canonical)
            except (OSError, ValueError):
                continue
    if engine == "pi":
        from . import pi_skills
        items.extend(pi_skills.inventory())
    return sorted(items, key=lambda item: (not item["managed"], item["name"], item["id"]))


def resolve(engine: str, id: str) -> Path:
    """Resolve only an ID still present in the current bounded inventory."""
    for item in inventory(engine):
        if item["id"] == id:
            return Path(item["path"])
    raise ValueError("Unknown skill ID")


def _zip_path(info: zipfile.ZipInfo) -> tuple[str, ...]:
    name = info.orig_filename
    if name != info.filename or "\\" in name or name.startswith("/"):
        raise ValueError("Unsafe ZIP path")
    parts = tuple(name[:-1].split("/") if name.endswith("/") else name.split("/"))
    for part in parts:
        if (not part or part in (".", "..") or part.endswith((".", " "))
                or any(ord(c) < 32 or c in ':*?<>|"' for c in part)
                or part.split(".")[0].casefold() in _RESERVED):
            raise ValueError("Unsafe ZIP path")
    if len(parts) > 16 or len(name.encode("utf-8")) > 1024:
        raise ValueError("ZIP path is too deep or long")
    return parts


def _validate_file(path: tuple[str, ...], data: bytes, mode: int) -> int:
    if path == ("SKILL.md",):
        _metadata(data)
        return 0o600
    if len(path) < 2 or path[0] not in _FOLDERS:
        raise ValueError("ZIP must contain only one skill and its support folders")
    suffix = Path(path[-1]).suffix.lower()
    script = path[0] == "scripts" and suffix in _SCRIPTS
    if suffix in _TEXT or script:
        _text(data)
        return 0o600 | (mode & 0o111 if script else 0)
    if suffix in _BINARY and data.startswith(_BINARY[suffix]):
        if suffix != ".webp" or data[8:12] == b"WEBP":
            return 0o600
    raise ValueError("Unsafe or unsupported skill file type")


def _unpack(data: bytes) -> dict[tuple[str, ...], tuple[bytes, int]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES * 2:
                raise ValueError("Too many ZIP entries")
            records = []
            explicit: set[tuple[str, ...]] = set()
            paths: dict[tuple[str, ...], tuple[tuple[str, ...], bool]] = {}
            total = count = 0
            for info in entries:
                parts = _zip_path(info)
                folded = tuple(p.casefold() for p in parts)
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                is_dir = info.is_dir()
                if (kind not in (0, stat.S_IFDIR if is_dir else stat.S_IFREG)
                        or mode & 0o7000 or info.flag_bits & (1 | 64)):
                    raise ValueError("ZIP links, special files, elevated modes and encryption are forbidden")
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ValueError("Unsupported ZIP compression")
                if folded in explicit:
                    raise ValueError("Duplicate ZIP path")
                explicit.add(folded)
                for length in range(1, len(parts) + 1):
                    key, original = folded[:length], parts[:length]
                    directory = length < len(parts) or is_dir
                    if key in paths and paths[key] != (original, directory):
                        raise ValueError("ZIP path collision")
                    paths[key] = (original, directory)
                total += info.file_size
                count += not is_dir
                if (total > MAX_EXPANDED_BYTES or count > MAX_FILES
                        or (is_dir and info.file_size)
                        or (info.file_size > 64 * 1024 and info.file_size > 200 * max(1, info.compress_size))):
                    raise ValueError("ZIP exceeds size, file count or compression ratio limits")
                records.append((info, parts, mode))
            skills = [parts for info, parts, _ in records if not info.is_dir() and parts[-1] == "SKILL.md"]
            if len(skills) != 1 or len(skills[0]) not in (1, 2):
                raise ValueError("ZIP must contain exactly one SKILL.md at root or in one enclosing folder")
            prefix = skills[0][:-1]
            files = {}
            for info, parts, mode in records:
                if parts[:len(prefix)] != prefix:
                    raise ValueError("ZIP contains more than one skill directory")
                relative = parts[len(prefix):]
                if info.is_dir():
                    if relative and relative[0] not in _FOLDERS:
                        raise ValueError("Unexpected skill support directory")
                    continue
                limit = MAX_SKILL_BYTES if relative == ("SKILL.md",) else MAX_EXPANDED_BYTES
                if info.file_size > limit:
                    raise ValueError("Skill file exceeds size limit")
                with archive.open(info) as stream:
                    content = stream.read(info.file_size + 1)
                if len(content) != info.file_size or len(content) > limit:
                    raise ValueError("Invalid expanded ZIP size")
                files[relative] = (content, _validate_file(relative, content, mode))
            return files
    except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, EOFError) as exc:
        raise ValueError("Invalid or unsupported ZIP archive") from exc


def _check_conflicts(engine: str, root: Path, name: str) -> None:
    if engine == "pi":
        from . import pi_skills
        if any(item["name"].casefold() == name for item in pi_skills.inventory(strict=True)):
            raise ValueError("A skill with this name already exists")
    for base in [root, *(_native_roots(engine) if engine != "pi" else [])]:
        for directory in _children(base, strict=True):
            if directory.name.casefold() == name:
                raise ValueError("A skill with this name already exists")
            if directory.name.startswith("."):
                continue
            try:
                existing, _ = _read_metadata(directory / "SKILL.md")
            except (OSError, ValueError):
                continue
            if existing.casefold() == name:
                raise ValueError("A skill with this name already exists")


def import_skill(engine: str, filename: str, content_b64: str) -> SkillItem:
    """Validate fully, then atomically publish a new QA-only directory.

    A sibling lock serializes cooperating importers, including other processes.
    No imported code is executed and no native directory is ever written.
    """
    _engine(engine)
    if not isinstance(filename, str) or (filename != "SKILL.md" and not re.fullmatch(r"[^/\\:]+\.zip", filename, re.IGNORECASE)):
        raise ValueError("Upload must be SKILL.md or a ZIP archive")
    if not isinstance(content_b64, str) or len(content_b64) > 4 * ((MAX_UPLOAD_BYTES + 2) // 3):
        raise ValueError("Skill upload exceeds 5 MiB")
    try:
        data = base64.b64decode(content_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid base64 skill upload") from exc
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Skill upload exceeds 5 MiB")
    files = {("SKILL.md",): (data, 0o600)} if filename == "SKILL.md" else _unpack(data)
    metadata = _metadata(files[("SKILL.md",)][0])
    root = _managed_root(engine)
    _safe_chain(root)
    _check_conflicts(engine, root, metadata[0])
    root.mkdir(parents=True, exist_ok=True)
    _safe_chain(root)
    lock = root / ".import-lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError("Another skill import is in progress") from exc
    temporary: Path | None = None
    try:
        _check_conflicts(engine, root, metadata[0])
        temporary = Path(tempfile.mkdtemp(prefix=".import-", dir=root))
        for parts, (content, mode) in files.items():
            path = temporary.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(content)
            path.chmod(mode)
        _safe_chain(root)
        _check_conflicts(engine, root, metadata[0])
        destination = root / metadata[0]
        temporary.rename(destination)
        temporary = None
        return _item(destination / "SKILL.md", True, metadata)
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)
        lock.rmdir()

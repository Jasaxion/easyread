"""Workspace filesystem boundary and optimistic, lossless browser writes."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import mimetypes
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import uuid

from . import WorkspaceError

MAX_TEXT = 8 * 1024 * 1024
MAX_FILE = 200 * 1024 * 1024
REVISION_DIR = '.easyread-revisions'
MAX_REVISION_ENTRIES = 300
MAX_REVISION_BYTES = 200 * 1024 * 1024


def relative(value, *, empty=False):
    if not isinstance(value, str) or '\\' in value or '\x00' in value or len(value) > 4096:
        raise WorkspaceError("Invalid workspace path")
    if not value and empty:
        return ""
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")) or ':' in value:
        raise WorkspaceError("Path must be relative to the project")
    return path.as_posix()


def target(root: Path, value, *, empty=False, missing=True):
    rel = relative(value, empty=empty)
    if root.is_symlink() or not root.is_dir():
        raise WorkspaceError("Workspace directory is unavailable", 404)
    current = root
    for part in PurePosixPath(rel).parts:
        current = current / part
        if current.is_symlink():
            raise WorkspaceError("Symbolic links are visible but cannot be followed", 403)
        if current.exists() and not (current.is_file() or current.is_dir()):
            raise WorkspaceError("Special files cannot be accessed", 403)
        if current.is_file() and current.stat().st_nlink != 1:
            raise WorkspaceError("Hard-linked files cannot be accessed", 403)
    if not missing and not current.exists():
        raise WorkspaceError("File not found", 404)
    return current


@contextmanager
def directory(path):
    """Pin each parent without following links on platforms with openat support."""
    path = Path(path).absolute()
    if os.open not in os.supports_dir_fd or not hasattr(os, 'O_NOFOLLOW'):
        # Windows lacks openat/O_NOFOLLOW: also check identities at the file open.
        for parent in (*reversed(path.parents), path):
            if parent.is_symlink() or not parent.is_dir():
                raise WorkspaceError('Directory is not accessible', 403)
        yield None
        return
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def at(path, fd):
    return (Path(path).name, {'dir_fd': fd}) if fd is not None else (str(path), {})


@contextmanager
def open_regular(path):
    """Read only regular, singly linked files; never open a FIFO or follow a link."""
    try:
        with directory(Path(path).parent) as parent:
            name, kwargs = at(path, parent)
            before = os.stat(name, follow_symlinks=False, **kwargs)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise WorkspaceError('Links and special files cannot be accessed', 403)
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
            fd = os.open(name, flags, **kwargs)
            with os.fdopen(fd, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                    raise WorkspaceError('File changed while opening', 409, 'file_conflict')
                yield stream
    except OSError:
        raise WorkspaceError('File changed or cannot be opened safely', 409, 'file_conflict') from None


def read_bytes(path, limit=MAX_FILE):
    with open_regular(path) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise WorkspaceError('File exceeds size limit', 413)
    return raw


def version(path):
    if not path.exists():
        return None
    if path.is_dir():
        return str(path.stat().st_mtime_ns)
    return hashlib.sha256(read_bytes(path)).hexdigest()


def _stat_stamp(info):
    # A read hint only, never a replacement for the content SHA used by writes.
    values = (info.st_dev, info.st_ino, info.st_ctime_ns, info.st_mtime_ns,
              info.st_size, info.st_mode, info.st_nlink)
    return hashlib.sha256(':'.join(map(str, values)).encode()).hexdigest()


def read(root, path, *, if_stamp=None):
    file = target(root, path, missing=False)
    with open_regular(file) as stream:
        before = _stat_stamp(os.fstat(stream.fileno()))
        if if_stamp == before:
            after = _stat_stamp(os.fstat(stream.fileno()))
            if before == after:
                return {"path": path, "stamp": before, "unchanged": True}
            # Revalidate the path/open if it changed during the cheap check.
            return read(root, path)
        raw = stream.read(MAX_FILE + 1)
        after = _stat_stamp(os.fstat(stream.fileno()))
    if len(raw) > MAX_FILE:
        raise WorkspaceError('File exceeds size limit', 413)
    mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    info = {"path": path, "size": len(raw), "mime": mime, "version": hashlib.sha256(raw).hexdigest(), "binary": True}
    # A racing read still has an exact content SHA, but no reusable stat hint.
    if before == after:
        info["stamp"] = before
    if len(raw) <= MAX_TEXT:
        try:
            content = raw.decode("utf-8")
            if '\x00' not in content:
                info.update(content=content, binary=False)
        except UnicodeDecodeError:
            pass
    return info


def publish(file, content, mode=0o644):
    """Publish complete bytes exclusively; an intervening writer always wins."""
    with directory(file.parent) as parent:
        name, kwargs = at(file, parent)
        tmp, _ = at(file.parent / ('.easyread-write-' + uuid.uuid4().hex), parent)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), mode & 0o777, **kwargs)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                if hasattr(os, 'fchmod'):
                    os.fchmod(stream.fileno(), mode & 0o777)
            link_args = {'src_dir_fd': parent, 'dst_dir_fd': parent} if parent is not None else {}
            os.link(tmp, name, follow_symlinks=False, **link_args)
        finally:
            os.unlink(tmp, **kwargs)


def revision_folder(root, required):
    base = target(root, REVISION_DIR)
    with directory(root) as parent:
        name, kwargs = at(base, parent)
        try:
            os.mkdir(name, mode=0o700, **kwargs)
        except FileExistsError:
            target(root, REVISION_DIR, missing=False)
    count, size = 0, 0
    for folder, dirs, names in os.walk(base, followlinks=False):
        for name in dirs + names:
            st = (Path(folder) / name).lstat()
            count += 1
            size += st.st_size if stat.S_ISREG(st.st_mode) else 0
            if stat.S_ISLNK(st.st_mode) or (stat.S_ISREG(st.st_mode) and st.st_nlink != 1):
                raise WorkspaceError('Remove links from recovery revisions before saving', 409, 'file_conflict')
            if count + 3 > MAX_REVISION_ENTRIES or size + required > MAX_REVISION_BYTES:
                raise WorkspaceError('Recovery revision budget exhausted. Delete unneeded files in ' + REVISION_DIR + ' before saving.', 409, 'file_conflict')
    if required > MAX_REVISION_BYTES or MAX_REVISION_ENTRIES < 3:
        raise WorkspaceError('Recovery revision budget exhausted', 409, 'file_conflict')
    folder = base / uuid.uuid4().hex
    with directory(base) as parent:
        name, kwargs = at(folder, parent)
        os.mkdir(name, mode=0o700, **kwargs)
    return folder


def relocate(source, dest):
    with directory(source.parent) as src, directory(dest.parent) as dst:
        a, _ = at(source, src)
        b, _ = at(dest, dst)
        kwargs = {'src_dir_fd': src, 'dst_dir_fd': dst} if src is not None and dst is not None else {}
        os.rename(a, b, **kwargs)


def write(root, path, content, expected):
    file = target(root, path, missing=False)
    if not file.is_file() or not isinstance(content, str):
        raise WorkspaceError("Expected a text file")
    if REVISION_DIR in PurePosixPath(path).parts:
        raise WorkspaceError('Recovery revisions cannot be overwritten; save a copy outside ' + REVISION_DIR, 409, 'file_conflict')
    original = read_bytes(file)
    if not isinstance(expected, str) or hashlib.sha256(original).hexdigest() != expected:
        raise WorkspaceError("File changed on disk. Your draft was not overwritten; reload or save a copy.", 409, "file_conflict")
    raw = content.encode("utf-8")
    if len(raw) > MAX_TEXT:
        raise WorkspaceError("Text exceeds 8 MiB editing limit", 413)
    if raw == original:
        return {"path": path, "version": expected}
    mode = stat.S_IMODE(file.stat().st_mode)
    recovery = revision_folder(root, len(original) + len(raw))
    disk, draft = recovery / 'disk', recovery / 'draft'
    publish(draft, raw, mode)
    # Not CAS: there is a brief absent-path window. Preserve the displaced inode
    # indefinitely, including writes through an Agent's already-open descriptor.
    try:
        relocate(file, disk)
        displaced = read_bytes(disk)
        if hashlib.sha256(displaced).hexdigest() != expected:
            publish(file, displaced, stat.S_IMODE(disk.stat().st_mode))
            raise WorkspaceError('File changed on disk', 409, 'file_conflict')
        publish(file, raw, mode)
        if read_bytes(disk) != displaced:
            raise WorkspaceError('Displaced file changed during save', 409, 'file_conflict')
    except (OSError, WorkspaceError) as exc:
        # Never restore over a concurrent creation. Both revisions remain visible.
        if not file.exists() and disk.exists():
            try:
                publish(file, read_bytes(disk), stat.S_IMODE(disk.stat().st_mode))
            except (OSError, WorkspaceError):
                pass
        rel = recovery.relative_to(root).as_posix()
        raise WorkspaceError(f'Save conflict: {exc}. Draft: {rel}/draft; displaced disk revision (if moved): {rel}/disk. Reload or save a copy.', 409, 'file_conflict') from None
    return {"path": path, "version": hashlib.sha256(raw).hexdigest()}


def create(root, path, kind="file", content=""):
    file = target(root, path)
    if file.exists():
        raise WorkspaceError("Destination already exists", 409)
    if not file.parent.is_dir():
        raise WorkspaceError("Parent directory does not exist")
    if kind == "directory":
        with directory(file.parent) as parent:
            name, kwargs = at(file, parent)
            os.mkdir(name, **kwargs)
    elif kind == "file" and isinstance(content, str):
        raw = content.encode("utf-8")
        if len(raw) > MAX_TEXT:
            raise WorkspaceError("Text exceeds 8 MiB editing limit", 413)
        try:
            publish(file, raw)
        except FileExistsError:
            raise WorkspaceError('Destination already exists', 409) from None
    else:
        raise WorkspaceError("Invalid file kind")
    return {"path": path, "version": version(file)}


def upload(root, path, raw):
    file = target(root, path)
    if len(raw) > MAX_FILE:
        raise WorkspaceError("File exceeds 200 MiB limit", 413)
    if not file.parent.is_dir():
        raise WorkspaceError("Parent directory does not exist")
    try:
        publish(file, raw)
    except FileExistsError:
        raise WorkspaceError("Destination already exists; rename the upload", 409) from None
    return {"path": path, "version": version(file)}


def move(root, path, destination):
    source, dest = target(root, path, missing=False), target(root, destination)
    if dest.exists():
        raise WorkspaceError("Destination already exists", 409)
    if not dest.parent.is_dir() or dest.is_relative_to(source):
        raise WorkspaceError("Invalid destination")
    source.rename(dest)
    return {"ok": True}


def delete(root, path):
    # Removing a symlink itself is safe; never follow its target.
    rel = relative(path)
    parent = PurePosixPath(rel).parent.as_posix()
    file = target(root, '' if parent == '.' else parent, empty=True, missing=False) / PurePosixPath(rel).name
    if file.is_symlink() or file.is_file():
        file.unlink()
    elif file.is_dir():
        shutil.rmtree(file)
    else:
        raise WorkspaceError("File not found", 404)
    return {"ok": True}


def tree(root, path="", *, if_version=None):
    base = target(root, path, empty=True, missing=False)
    if not base.is_dir():
        raise WorkspaceError("Expected a directory")
    entries = []
    digest = hashlib.sha256()
    def walk(directory):
        for file in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
            if len(entries) >= 15000:
                raise WorkspaceError("Directory contains too many entries; select a smaller subtree", 413)
            try:
                s = file.lstat()
            except FileNotFoundError:
                continue
            kind = "symlink" if stat.S_ISLNK(s.st_mode) else "directory" if stat.S_ISDIR(s.st_mode) else "file"
            rel = file.relative_to(root).as_posix()
            v = f"{s.st_mtime_ns}:{s.st_size}"
            entries.append({"path": rel, "name": file.name, "kind": kind, "size": s.st_size, "version": v})
            digest.update(f"{rel}:{kind}:{v}\n".encode())
            if kind == "directory":
                walk(file)
    walk(base)
    current = digest.hexdigest()
    if if_version == current:
        return {"unchanged": True, "version": current}
    return {"entries": entries, "version": current}

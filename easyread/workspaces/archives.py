"""Versioned project archives; no extraction or execution before full validation."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import time
import zipfile

from . import WorkspaceError
from .files import at, directory, open_regular, relative

MAX_EXPANDED = 512 * 1024 * 1024
MAX_ENTRIES = 50000


def _add(archive, source, prefix, budget):
    """Count directories too, and enforce byte limits on the actual nofollow reads."""
    def add(path, name):
        budget[0] += 1
        if budget[0] > MAX_ENTRIES:
            raise WorkspaceError('Too many archive entries', 413)
        with directory(path.parent) as parent:
            entry, kwargs = at(path, parent)
            metadata = os.stat(entry, follow_symlinks=False, **kwargs)
        mode = metadata.st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)) or (stat.S_ISREG(mode) and metadata.st_nlink != 1):
            raise WorkspaceError('Cannot export links or special files')
        timestamp = time.localtime(max(315532800, min(metadata.st_mtime, 4354819199)))[:6]
        info = zipfile.ZipInfo(name + ('/' if stat.S_ISDIR(mode) else ''), timestamp)
        info.create_system = 3
        info.external_attr = (mode & 0xFFFF) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        if stat.S_ISDIR(mode):
            info.external_attr |= 0x10
            archive.writestr(info, b'')
            with directory(path) as fd:
                with os.scandir(fd if fd is not None else path) as entries:
                    for child in entries:
                        add(path / child.name, name + '/' + child.name)
        else:
            with open_regular(path) as src, archive.open(info, 'w') as dest:
                while True:
                    chunk = src.read(min(1024 * 1024, MAX_EXPANDED - budget[1] + 1))
                    if not chunk:
                        break
                    budget[1] += len(chunk)
                    if budget[1] > MAX_EXPANDED:
                        raise WorkspaceError('Project exceeds 512 MiB archive limit; use Git or filesystem backup for larger projects', 413)
                    dest.write(chunk)
    try:
        add(source, prefix)
    except OSError as exc:
        raise WorkspaceError('Project changed or cannot be safely exported: ' + str(exc), 409) from None


def export_project(store, sessions, pid):
    project = store.describe(pid)
    with tempfile.TemporaryDirectory(prefix='easyread-project-export-') as tmp:
        state = Path(tmp).resolve() / 'state'
        state.mkdir()
        sessions.export_state(pid, state)
        output = io.BytesIO()
        manifest = json.dumps({'format': 'easyread-research-workspace', 'schema': 1, 'project': project}, ensure_ascii=False).encode('utf-8')
        budget = [1, len(manifest)]
        if budget[1] > MAX_EXPANDED:
            raise WorkspaceError('Project manifest exceeds archive limit', 413)
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', manifest)
            _add(archive, store.root(pid), 'workspace', budget)
            _add(archive, state, 'state', budget)
        return output.getvalue()


def import_project(store, sessions, raw):
    if len(raw) > 200 * 1024 * 1024:
        raise WorkspaceError('Archive exceeds 200 MiB upload limit', 413)
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise WorkspaceError('Invalid ZIP archive') from None
    with archive, tempfile.TemporaryDirectory(prefix='easyread-project-import-') as tmp:
        base = Path(tmp).resolve()
        seen, names, total, layout = set(), [], 0, {}
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise WorkspaceError('Too many archive entries', 413)
        for info in infos:
            name = info.filename.rstrip('/')
            relative(name)
            if name != 'manifest.json' and name.split('/')[0] not in ('workspace', 'state'):
                raise WorkspaceError('Unexpected archive entry')
            key = name.casefold()
            if key in seen:
                raise WorkspaceError('Duplicate archive path')
            seen.add(key)
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
                raise WorkspaceError('Encrypted archives, links and special files are not supported')
            if info.is_dir() != (kind == stat.S_IFDIR) and kind not in (0, stat.S_IFREG):
                raise WorkspaceError('Invalid directory entry')
            total += info.file_size
            if total > MAX_EXPANDED or info.file_size > MAX_EXPANDED:
                raise WorkspaceError('Archive expansion exceeds safety limit', 413)
            for parent in reversed(PurePosixPath(name).parents):
                if parent.as_posix() != '.':
                    key = parent.as_posix().casefold()
                    if layout.get(key) == 'file':
                        raise WorkspaceError('File/directory archive collision')
                    layout[key] = 'directory'
            key = name.casefold()
            kind_name = 'directory' if info.is_dir() else 'file'
            if key in layout and layout[key] != kind_name:
                raise WorkspaceError('File/directory archive collision')
            layout[key] = kind_name
            if len(layout) > MAX_ENTRIES:
                raise WorkspaceError('Too many archive entries including implicit directories', 413)
            names.append((info, name, mode))
        directory_modes = []
        for info, name, mode in names:
            path = base / name
            # A file/directory prefix collision fails without touching destination data.
            try:
                if info.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as src, path.open('xb') as dest:
                        shutil.copyfileobj(src, dest)
                permissions = mode & 0o777 if stat.S_IFMT(mode) else (0o755 if info.is_dir() else 0o644)
                if info.is_dir():
                    directory_modes.append((path, permissions))
                else:
                    path.chmod(permissions)
            except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                raise WorkspaceError('Invalid archive layout or content: ' + str(exc)) from None
        try:
            manifest = json.loads((base / 'manifest.json').read_text('utf-8'))
            if manifest.get('format') != 'easyread-research-workspace' or manifest.get('schema') != 1:
                raise ValueError('Unsupported archive schema')
            project = manifest['project']
            store.validate(project)
            ids = project.get('papers', [])
            if not isinstance(ids, list) or len(ids) > 10000 or any(not isinstance(x, str) or len(x) > 200 for x in ids):
                raise ValueError('Invalid paper references')
            if not (base / 'workspace').is_dir():
                raise ValueError('Workspace files missing')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise WorkspaceError('Invalid project manifest: ' + str(exc)) from None
        if (base / 'workspace' / '.git').exists():
            from .git import validate_metadata
            validate_metadata(base / 'workspace')
        # Apply directory modes only after extracting children and validating metadata.
        for path, permissions in reversed(sorted(directory_modes, key=lambda item: len(item[0].parts))):
            path.chmod(permissions)
        created = store.allocate(project)
        pid = created['id']
        try:
            shutil.copytree(base / 'workspace', store.root(pid), dirs_exist_ok=True)
            # Imported content is never run here: no bd init, Git hooks or TeX compile.
            # Git browser operations use sanitized settings/hooks in git.py.
            from ..library.store import write_json_atomic
            created['papers'] = ids
            created['initialization'] = project.get('initialization', {})
            created['imported'] = True
            write_json_atomic(store.meta(pid) / 'project.json', created)
            if (base / 'state').is_dir():
                sessions.import_state(pid, base / 'state')
            return {'project': created}
        except Exception:
            store.delete(pid)
            raise

"""Explicit project Git actions with hooks, filters and config includes disabled."""
from __future__ import annotations

import configparser
from contextlib import contextmanager
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from urllib.parse import urlsplit

from . import WorkspaceError
from . import files
from .files import target


def executable():
    return shutil.which(os.environ.get("EASYREAD_GIT_COMMAND", "git"))


def safe_url(url):
    if not isinstance(url, str) or not url or len(url) > 2048 or any(ord(c) < 32 for c in url) or url.startswith('-'):
        raise WorkspaceError("Invalid Git remote URL")
    parsed = urlsplit(url)
    # Local bare repositories are useful offline and require an explicit user action.
    if Path(url).is_absolute():
        return url
    if parsed.scheme in ("https", "http", "ssh") and parsed.hostname and not parsed.password:
        return url
    if re.fullmatch(r"[\w.-]+@[\w.-]+:[\w./~-]+", url):
        return url
    raise WorkspaceError("Git remote must use HTTPS, SSH or an absolute local path")


def environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_ATTR_NOSYSTEM="1")
    return env


def validate_metadata(root):
    """Imported Git metadata may contain data, never filesystem indirections."""
    gitdir = target(root, '.git', missing=False)
    if not gitdir.is_dir():
        raise WorkspaceError('Git worktree indirection is not supported')
    forbidden = {'commondir', 'gitdir', 'objects/info/alternates', 'objects/info/http-alternates'}
    count = 0
    for folder, dirs, names in os.walk(gitdir, followlinks=False):
        for name in dirs + names:
            path = Path(folder) / name
            mode = path.lstat()
            count += 1
            if count > 50000:
                raise WorkspaceError('Too many Git metadata entries', 413)
            if path.relative_to(gitdir).as_posix().casefold() in forbidden:
                raise WorkspaceError('Git metadata indirection is not supported: ' + name, 403)
            if not (stat.S_ISREG(mode.st_mode) or stat.S_ISDIR(mode.st_mode)) or (stat.S_ISREG(mode.st_mode) and mode.st_nlink != 1):
                raise WorkspaceError('Git metadata links and special files are not supported', 403)
    return gitdir


def safe_config(original):
    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read_string(original.decode("utf-8"))
    except (UnicodeError, configparser.Error):
        raise WorkspaceError("Invalid Git config") from None
    safe = ['[core]\nrepositoryformatversion = 0\nbare = false\nfilemode = true\nlogallrefupdates = true\n',
            '[user]\nname = EasyRead Research\nemail = research@easyread.local\n']
    for section in parser.sections():
        match = re.fullmatch(r'remote "([\w.-]+)"', section)
        if match:
            remote = match[1]
            url = parser.get(section, 'url', fallback='')
            try:
                safe_url(url)
            except WorkspaceError:
                continue
            escaped = url.replace('\\', '\\\\').replace('"', '\\"')
            safe.append(f'[{section}]\nurl = "{escaped}"\nfetch = +refs/heads/*:refs/remotes/{remote}/*\n')
        if re.fullmatch(r'branch "[\w./-]+"', section):
            remote = parser.get(section, 'remote', fallback='')
            merge = parser.get(section, 'merge', fallback='')
            if re.fullmatch(r'[\w.-]+', remote) and re.fullmatch(r'refs/heads/[\w./-]+', merge):
                safe.append(f'[{section}]\nremote = {remote}\nmerge = {merge}\n')
    return ''.join(safe).encode()


def restore_config(source, destination):
    # Link then unlink is a no-clobber rename for a regular file, preserving its
    # inode/mode/mtime and any writer that still holds the original descriptor.
    with files.directory(source.parent) as src, files.directory(destination.parent) as dst:
        a, _ = files.at(source, src)
        b, _ = files.at(destination, dst)
        kwargs = {'src_dir_fd': src, 'dst_dir_fd': dst} if src is not None and dst is not None else {}
        os.link(a, b, follow_symlinks=False, **kwargs)
        os.unlink(a, **({'dir_fd': src} if src is not None else {}))


@contextmanager
def sanitized(root):
    gitdir = validate_metadata(root)
    cfg = target(root, '.git/config', missing=False)
    original = files.read_bytes(cfg, files.MAX_TEXT)
    safe = safe_config(original)
    # Only conflicts retain backups; status polling must not consume revision
    # budget. Reserve room before touching config, and never clobber restoration.
    recovery = files.revision_folder(gitdir, len(original) + len(safe))
    backup, temporary = recovery / 'original-config', recovery / 'temporary-config'
    displaced = False
    conflict = False
    try:
        files.relocate(cfg, backup)
        displaced = True
        if files.read_bytes(backup, files.MAX_TEXT) != original:
            raise WorkspaceError('Git config changed before operation', 409, 'file_conflict')
        files.publish(cfg, safe, stat.S_IMODE(backup.stat().st_mode))
        yield cfg
    finally:
        if displaced:
            try:
                # Move first, compare second: a racing replacement is retained,
                # rather than destroyed by a check-then-replace restoration.
                if cfg.exists() or cfg.is_symlink():
                    files.relocate(cfg, temporary)
                    conflict = files.read_bytes(temporary, files.MAX_TEXT) != safe
                else:
                    conflict = True
            except (OSError, WorkspaceError):
                conflict = True
            try:
                restore_config(backup, cfg)
            except (OSError, WorkspaceError):
                conflict = True
            if conflict:
                raise WorkspaceError('Git config changed during operation. Recovery files: ' + recovery.relative_to(root).as_posix(), 409, 'file_conflict') from None
            # These are sanitizer-owned identical bytes, not user revisions.
            # Late writes through a handle to this temporary file are not covered.
            temporary.unlink(missing_ok=True)
        recovery.rmdir()


def run(root, args, *, timeout=60, network=False, sanitize=True):
    exe = executable()
    if not exe:
        raise WorkspaceError("Git is not installed", 503)
    command = [exe, '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false',
               '-c', 'credential.helper=', '-c', 'protocol.ext.allow=never', '-c', 'protocol.file.allow=always',
               '-c', 'core.pager=cat', '-c', 'gc.auto=0', '-c', 'submodule.recurse=false',
               '-c', 'diff.ignoreSubmodules=all'] + args
    def invoke():
        env = environment()
        env.update(GIT_DIR=str((root / '.git').absolute()), GIT_COMMON_DIR=str((root / '.git').absolute()),
                   GIT_WORK_TREE=str(root.absolute()))
        try:
            proc = subprocess.run(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise WorkspaceError("Git operation timed out", 408) from None
        output = (proc.stdout + proc.stderr).decode('utf-8', errors='replace')[-100000:]
        if proc.returncode:
            raise WorkspaceError(output or "Git operation failed")
        return output.strip()
    if sanitize:
        with sanitized(root):
            return invoke()
    return invoke()


def initialize(root):
    result = run(root, ['init', '-b', 'main'], sanitize=False)
    run(root, ['config', 'user.name', 'EasyRead Research'], sanitize=False)
    run(root, ['config', 'user.email', 'research@easyread.local'], sanitize=False)
    return result


def status(root):
    if not (root / '.git').exists():
        return {"branch": "", "status": "Not initialized", "remotes": [], "installed": bool(executable())}
    return {"branch": run(root, ['symbolic-ref', '--short', 'HEAD']),
            "status": run(root, ['status', '--short']),
            "remotes": run(root, ['remote', '-v']).splitlines(), "installed": True}


def action(root, body):
    action = body.get('action', 'status')
    if action == 'status':
        return status(root)
    if action == 'remote':
        url = safe_url(body.get('url'))
        # Use sanitized config for reading, but preserve the original custom config.
        validate_metadata(root)
        cfg = target(root, '.git/config', missing=False)
        original = files.read_bytes(cfg, files.MAX_TEXT)
        parser = configparser.RawConfigParser(strict=False)
        try:
            parser.read_string(original.decode('utf-8'))
        except (UnicodeError, configparser.Error):
            raise WorkspaceError('Invalid Git config') from None
        if not parser.has_section('remote "origin"'):
            parser.add_section('remote "origin"')
        parser.set('remote "origin"', 'url', url)
        parser.set('remote "origin"', 'fetch', '+refs/heads/*:refs/remotes/origin/*')
        import io
        stream = io.StringIO()
        parser.write(stream)
        import hashlib
        files.write(root / '.git', 'config', stream.getvalue(), hashlib.sha256(original).hexdigest())
        return status(root)
    if action == 'commit':
        message = body.get('message', '')
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise WorkspaceError("Enter a commit message")
        run(root, ['add', '--all', '--', '.'])
        output = run(root, ['commit', '-m', message])
    elif action in ('pull', 'push'):
        branch = run(root, ['symbolic-ref', '--short', 'HEAD'])
        if not re.fullmatch(r'[\w./-]+', branch) or branch.startswith('-'):
            raise WorkspaceError("Invalid Git branch")
        args = ['pull', '--ff-only', 'origin', branch] if action == 'pull' else ['push', 'origin', f'HEAD:refs/heads/{branch}']
        output = run(root, args, timeout=120, network=True)
    else:
        raise WorkspaceError("Unknown Git action")
    return {**status(root), "output": output}

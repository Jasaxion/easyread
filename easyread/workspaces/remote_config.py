"""Private local SSH configuration and offline project references.

The registry never contains private key contents, passwords or remote CLI auth.
Its file lock protects atomic metadata updates only. Per-server lifecycle
reservations serialize creation against reconfiguration/deletion separately.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import ipaddress
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import tempfile
import threading

from filelock import FileLock, Timeout

from . import WorkspaceError

FIELDS = {'name', 'host', 'port', 'user', 'workspace', 'identity_file', 'python'}
TARGET_FIELDS = FIELDS - {'name'}
ID = re.compile(r'^[a-z]{20}$')


def identifier():
    return ''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(20))


def validate(body):
    if not isinstance(body, dict) or set(body) - FIELDS:
        raise WorkspaceError('Only name, host, port, user, workspace, identity_file and python are accepted')
    result = dict(body)
    for key in ('name', 'host', 'user', 'workspace'):
        value = result.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > (4096 if key == 'workspace' else 200):
            raise WorkspaceError(f'{key} is required')
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise WorkspaceError(f'Invalid {key}')
    result['name'] = result['name'].strip()
    host = result['host']
    if ':' in host:
        try:
            valid_host = '%' not in host and ipaddress.ip_address(host).version == 6
        except ValueError:
            valid_host = False
    else:
        valid_host = bool(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*', host))
    if not valid_host:
        raise WorkspaceError('Host must be a hostname or IP address, not an SSH command or alias configuration')
    if not re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]*', result['user']):
        raise WorkspaceError('Invalid SSH user')
    port = result.get('port', 22)
    if type(port) is not int or not 1 <= port <= 65535:
        raise WorkspaceError('SSH port must be an integer between 1 and 65535')
    result['port'] = port
    workspace = result['workspace']
    if (not workspace.startswith('/') or workspace.startswith('//') or workspace == '/'
            or any(p in ('', '.', '..') for p in workspace[1:].split('/'))):
        raise WorkspaceError('Workspace must be a normalized absolute POSIX directory, not /')
    result['workspace'] = str(PurePosixPath(workspace))
    python = result.get('python', 'python3')
    if (not isinstance(python, str) or len(python) > 4096
            or not re.fullmatch(r'(?:/[a-zA-Z0-9_./-]+|python3(?:\.[0-9]+)?)', python)
            or '..' in python.split('/')):
        raise WorkspaceError('Python must be python3, python3.x or an absolute executable path')
    result['python'] = python
    identity = result.get('identity_file', '')
    if not isinstance(identity, str) or len(identity) > 4096 or '%' in identity or any(ord(c) < 32 for c in identity):
        raise WorkspaceError('Invalid identity file path')
    if identity:
        path = Path(identity).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise WorkspaceError('Identity file must be an existing absolute local file path')
        result['identity_file'] = str(path)
    else:
        result['identity_file'] = ''
    return result


class Registry:
    def __init__(self, libroot):
        self.directory = Path(libroot) / '.remote-workspaces'
        if self.directory.is_symlink():
            raise WorkspaceError('Invalid remote configuration directory', 403)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / 'registry.json'
        self.lock = threading.RLock()
        self.file_lock = FileLock(str(self.directory / 'registry.lock'), timeout=180)

    def read(self):
        if self.path.is_symlink():
            raise WorkspaceError('Invalid remote registry', 403)
        if not self.path.exists():
            return {'version': 1, 'servers': {}, 'projects': {}}
        try:
            data = json.loads(self.path.read_text('utf-8'))
            if data['version'] != 1 or not isinstance(data['servers'], dict) or not isinstance(data['projects'], dict):
                raise ValueError()
            return data
        except (ValueError, KeyError, OSError):
            raise WorkspaceError('Remote configuration is unreadable; it was not replaced', 409) from None

    @contextmanager
    def locked(self):
        with self.lock:
            try:
                self.file_lock.acquire()
            except Timeout:
                raise WorkspaceError('Remote registry is busy; retry after the current metadata update', 409, 'remote_busy') from None
            try:
                yield self.read()
            finally:
                self.file_lock.release()

    @contextmanager
    def transaction(self):
        with self.locked() as data:
            yield data
            self.write(data)

    def write(self, data):
        fd, temporary = tempfile.mkstemp(prefix='.registry-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def server(self, sid, data=None):
        data = self.read() if data is None else data
        if not isinstance(sid, str) or not ID.fullmatch(sid) or sid not in data['servers']:
            raise WorkspaceError('Remote server not found', 404)
        return dict(data['servers'][sid])

    def project(self, pid):
        return self.read()['projects'].get(pid)

    def listing(self, q=''):
        data = self.read()
        out = []
        for project in data['projects'].values():
            project = dict(project)
            sid = project['remote']['server_id']
            project['remote'] = {**project['remote'], 'server_name': data['servers'].get(sid, {}).get('name', sid)}
            if str(q).casefold() in (project['name'] + ' ' + project['description']).casefold():
                out.append(project)
        return out

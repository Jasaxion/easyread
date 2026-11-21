"""SSH server configuration and lifecycle reservations.

Registry/map locks protect short state transitions only, never network I/O.
The per-server control file lock excludes creation from retarget/delete even
across local processes; ordinary project requests never acquire that lock.
"""
from __future__ import annotations

from contextlib import contextmanager
import shutil
import threading
import time

from filelock import FileLock, Timeout

from ..library.store import now_iso
from . import WorkspaceError
from .remote_config import Registry, TARGET_FIELDS, identifier, validate
from .remote_transport import unavailable

IDLE_SECONDS = 300


class RemoteServers:
    def __init__(self, libroot):
        self.registry = Registry(libroot)
        self.libroot = libroot
        self.lock = threading.RLock()
        self.connections = {}
        self.leases = {}
        self.transitions = set()
        self.stopped = threading.Event()
        self.reaper = threading.Thread(target=self._reap, name='workspace-ssh-idle', daemon=True)
        self.reaper.start()

    def servers(self):
        data = self.registry.read()
        with self.lock:
            return {'servers': [{**server, 'connected': bool(self.connections.get(sid) and self.connections[sid].connected)}
                                for sid, server in data['servers'].items()], 'ssh_available': bool(shutil.which('ssh'))}

    @contextmanager
    def control(self, sid, *, transition=False):
        self.registry.server(sid)  # Validate before using the id as a lock filename.
        reservation = FileLock(str(self.registry.directory / (sid + '.control.lock')), timeout=0)
        try:
            reservation.acquire()
        except Timeout:
            raise WorkspaceError('A creation or server transition is in progress; retry when it finishes', 409, 'remote_busy') from None
        reserved = False
        try:
            with self.registry.locked() as data, self.lock:
                config = self.registry.server(sid, data)
                if self.stopped.is_set():
                    raise unavailable()
                if transition:
                    if self.leases.get(sid):
                        raise WorkspaceError('Remote requests are active; wait before this operation', 409, 'remote_busy')
                    self.transitions.add(sid)
                    reserved = True
            yield config
        finally:
            if reserved:
                with self.lock:
                    self.transitions.discard(sid)
            reservation.release()

    def save_server(self, body, sid=None):
        if not isinstance(body, dict):
            raise WorkspaceError('Expected a JSON object')
        if sid is None:
            config = {**validate(body), 'id': identifier(), 'suspended': False}
            sid = config['id']
            with self.registry.transaction() as data:
                data['servers'][sid] = config
        else:
            with self.control(sid, transition=True) as old:
                config = validate({**{k: old[k] for k in TARGET_FIELDS | {'name'}}, **body})
                changed = any(config[k] != old[k] for k in TARGET_FIELDS)
                if changed:
                    self._require_unused(sid)
                    self._disconnect_connection(sid)
                with self.registry.transaction() as data:
                    current = self.registry.server(sid, data)
                    config.update(id=sid, suspended=current.get('suspended', False))
                    if not changed and 'last_check' in current:
                        config['last_check'] = current['last_check']
                    data['servers'][sid] = config
        return {'server': next(s for s in self.servers()['servers'] if s['id'] == sid)}

    def _require_unused(self, sid):
        if any(p['remote']['server_id'] == sid for p in self.registry.read()['projects'].values()):
            raise WorkspaceError('Delete or recover existing projects before changing the server target or removing it', 409, 'remote_in_use')

    def delete_server(self, sid):
        with self.control(sid, transition=True):
            self._require_unused(sid)
            self._disconnect_connection(sid)
            with self.registry.transaction() as data:
                del data['servers'][sid]
        return {'ok': True}

    @contextmanager
    def connection(self, sid):
        retired = None
        with self.registry.locked() as data, self.lock:
            if self.stopped.is_set():
                raise unavailable()
            config = self.registry.server(sid, data)
            if sid in self.transitions:
                raise WorkspaceError('This server is changing connection state; retry when it finishes', 409, 'remote_busy')
            if config.get('suspended'):
                raise WorkspaceError('This server was disconnected. Connect explicitly before using remote projects.', 409, 'remote_disconnected')
            conn = self.connections.get(sid)
            if conn is None or conn._closed or (conn.proc and conn.proc.poll() is not None):
                if self.leases.get(sid):
                    raise unavailable()
                retired = conn
                conn = self.connections[sid] = self._new_connection(config)
            self.leases[sid] = self.leases.get(sid, 0) + 1
        try:
            if retired:
                retired.close()
            yield conn
        finally:
            with self.lock:
                self.leases[sid] -= 1
                conn.last_used = time.monotonic()

    def check(self, sid, engine='codex'):
        if engine not in ('codex', 'claude', 'pi'):
            raise WorkspaceError('Choose Codex, Claude Code or Pi Agent')
        config = self.registry.server(sid)
        try:
            with self.connection(sid) as conn:
                config = conn.config
                result = conn.json('POST', '/check', {'engine': engine})
        except WorkspaceError as exc:
            result = {'ok': False, 'checks': [{'name': 'ssh_worker', 'ok': False, 'required': True, 'message': str(exc)}],
                      'checked_at': now_iso(), 'engine': engine}
        with self.registry.transaction() as data:
            current = data['servers'].get(sid)
            if current and all(current[k] == config[k] for k in TARGET_FIELDS):
                current['last_check'] = result
        return result

    def connect(self, sid):
        with self.control(sid):
            with self.registry.transaction() as data:
                data['servers'][sid]['suspended'] = False
            with self.connection(sid) as conn:
                conn.json('GET', '/status')
        return {'ok': True}

    def _disconnect_connection(self, sid):
        # A transition reservation rejects new same-server requests while this
        # probe runs, but leaves cancellation on every other server unblocked.
        with self.lock:
            conn = self.connections.get(sid)
        if conn:
            if conn.connected and conn.json('GET', '/status').get('busy'):
                raise WorkspaceError('Stop running remote AI/LaTeX tasks before disconnecting', 409, 'remote_busy')
            conn.close()
        with self.lock:
            self.connections.pop(sid, None)

    def disconnect(self, sid, *, suspend=True):
        with self.control(sid, transition=True):
            self._disconnect_connection(sid)
            if suspend:
                with self.registry.transaction() as data:
                    data['servers'][sid]['suspended'] = True
        return {'ok': True}

    def busy(self):
        with self.lock:
            return bool(self.transitions or any(self.leases.values()) or any(c.connected for c in self.connections.values()))

    def _reap(self):
        while not self.stopped.wait(15):
            with self.lock:
                ids = [sid for sid, conn in self.connections.items()
                       if not self.leases.get(sid) and time.monotonic() - conn.last_used > IDLE_SECONDS]
            for sid in ids:
                try:
                    self.disconnect(sid, suspend=False)
                except (WorkspaceError, OSError):
                    pass

    def close(self):
        self.stopped.set()
        with self.lock:
            connections = list(self.connections.values())
            self.connections.clear()
        for conn in connections:
            conn.close()
        self.reaper.join(5)

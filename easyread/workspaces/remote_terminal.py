"""Opaque, access-session-owned terminal handles pinned to one SSH generation.

The browser speaks ttyd unchanged. Only an explicit terminal POST can allocate;
HTTP/WS attachment never opens a native session. A disconnected iframe stops
its native process (there is no VTE snapshot/reconnect persistence).
"""
from __future__ import annotations

import os
import secrets
import socket
import threading
import time

from . import WorkspaceError
from .remote_terminal_worker import OWNER_HEADER, URL, denied
from .remote_terminal_relay import relay


def enabled():
    # Remote ttyd is sufficient; do not inspect the deployment host's PATH.
    return os.environ.get('EASYREAD_TERMINAL') == '1'


class RemoteTerminals:
    def __init__(self, remotes, access):
        self.remotes, self.access = remotes, access
        self.lock = threading.RLock()
        self.entries = {}
        self.handles = {}
        self.revoked = set()
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.reaper = threading.Thread(target=self._reap, name='workspace-remote-terminals', daemon=True)
        self.reaper.start()

    @staticmethod
    def path(entry):
        _, pid, sid = entry['scope']
        return f'/api/workspaces/{pid}/sessions/{sid}/terminal'

    @staticmethod
    def headers(entry):
        return {OWNER_HEADER: entry['owner']}

    def valid(self, entry):
        with self.lock:
            return bool(not self.stopped.is_set() and not entry['closed'] and enabled()
                        and self.access and self.access.valid(entry['access_sid']) and entry['conn'] and entry['conn'].connected
                        and self.entries.get(entry['scope']) is entry)

    def open(self, server, pid, sid, access_sid):
        from .sessions import SID
        if not SID.fullmatch(sid):
            raise WorkspaceError('Invalid session ID')
        if not enabled():
            raise WorkspaceError('Enable EASYREAD_TERMINAL=1 on the EasyRead deployment and remote host', 409, 'remote_terminal_unavailable')
        scope = (server, pid, sid)
        with self.lock:
            if self.stopped.is_set() or access_sid in self.revoked or not self.access or not self.access.valid(access_sid):
                raise denied()
            entry = self.entries.get(scope)
            if entry:
                if entry['access_sid'] != access_sid or entry['closed']:
                    raise denied()
                if entry['opening']:
                    raise WorkspaceError('Terminal is still opening', 409, 'remote_busy')
                existing = True
            else:
                if sum(e['scope'][0] == server for e in self.entries.values()) >= 4:
                    raise WorkspaceError('Remote terminal capacity reached', 429, 'remote_terminal_capacity')
                entry = {'scope': scope, 'access_sid': access_sid, 'owner': secrets.token_hex(32),
                         'opening': True, 'closed': False, 'conn': None, 'lease': None,
                         'sockets': set(), 'cleanup': threading.Lock(), 'created': time.monotonic()}
                self.entries[scope] = entry
                existing = False
        if existing:
            if not self.valid(entry):
                self._retire(entry)
                raise denied()
            status = entry['conn'].json('GET', self.path(entry) + '/status', headers=self.headers(entry), require_connected=True)
            if not self.valid(entry) or not status.get('active') or status.get('scope') != [pid, sid]:
                self._retire(entry)
                raise denied()
            return dict(entry['result'])
        try:
            lease = self.remotes.connection(server)
            conn = lease.__enter__()
            with self.lock:
                entry.update(lease=lease, conn=conn)
            # Only this first, explicit open may start the configured worker.
            conn.start()
            if not self.valid(entry):
                raise denied()
            result = conn.json('POST', self.path(entry), {}, headers=self.headers(entry), require_connected=True)
            url = result.get('url')
            if not isinstance(url, str) or not URL.fullmatch(url) or result.get('scope') != [pid, sid]:
                raise WorkspaceError('Invalid remote terminal identity', 502, 'remote_protocol')
            with self.lock:
                if not self.valid(entry) or url in self.handles:
                    raise denied()
                result = {key: result[key] for key in ('url', 'engine', 'native_id', 'scope') if key in result}
                entry.update(result=result, url=url)
                self.handles[url] = entry
            return dict(result)
        except Exception:
            self._retire(entry)
            raise
        finally:
            with self.lock:
                entry['opening'] = False
            if entry['closed']:
                self._cleanup(entry)

    def owns(self, path):
        # Malformed/unknown handles go to the local terminal relay, which denies
        # them; there is never a remote target inferred from a browser URL.
        prefix = '/'.join(path.split('/')[:3]) + '/'
        with self.lock:
            return prefix in self.handles

    def relay(self, handler, access_sid):
        return relay(self, handler, access_sid)

    def _retire(self, entry):
        with self.lock:
            was_closed = entry['closed']
            entry['closed'] = True
            for sock in list(entry['sockets']):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        if not was_closed:
            self.wake.set()

    def _cleanup(self, entry, *, wait=False):
        if not entry['cleanup'].acquire(blocking=wait):
            return False
        try:
            with self.lock:
                conn = entry['conn']
                if self.entries.get(entry['scope']) is not entry:
                    return True
                if entry['opening']:
                    return False
            if conn and conn.connected:
                # A lost open response still owns this reservation. A scoped
                # tombstone closes just that token, never a replacement writer.
                conn.json('POST', self.path(entry) + '/close', {}, headers=self.headers(entry), require_connected=True)
            with self.lock:
                self.entries.pop(entry['scope'], None)
                if entry.get('url'):
                    self.handles.pop(entry['url'], None)
                lease, entry['lease'] = entry['lease'], None
            if lease:
                lease.__exit__(None, None, None)
            return True
        except (WorkspaceError, OSError):
            # Keep the lease and deny new opens until cleanup is acknowledged
            # or this exact worker generation dies. Only this idempotent scoped
            # close is retried, never an open or an unscoped session cancel.
            return False
        finally:
            entry['cleanup'].release()

    def close_scope(self, server, pid, sid, access_sid):
        with self.lock:
            entry = self.entries.get((server, pid, sid))
            if not entry:
                return False
            if entry['access_sid'] != access_sid:
                raise denied()
        self._retire(entry)
        if not self._cleanup(entry, wait=True):
            raise WorkspaceError('Terminal cleanup is pending; retry after it stops', 409, 'remote_busy')
        return True

    def revoke(self, access_sid=None):
        with self.lock:
            if access_sid is not None:
                self.revoked.add(access_sid)
            entries = [e for e in self.entries.values() if access_sid is None or e['access_sid'] == access_sid]
        for entry in entries:
            self._retire(entry)
        for entry in entries:
            self._cleanup(entry)

    def _reap(self):
        while not self.stopped.is_set():
            self.wake.wait(2)
            self.wake.clear()
            with self.lock:
                self.revoked = {sid for sid in self.revoked if self.access and self.access.valid(sid)}
                entries = list(self.entries.values())
            for entry in entries:
                if entry['opening']:
                    continue
                if not self.valid(entry) or time.monotonic() - entry['created'] > 12 * 3600:
                    self._retire(entry)
                elif time.monotonic() - entry.get('checked', 0) > 15:
                    try:
                        status = entry['conn'].json('GET', self.path(entry) + '/status', headers=self.headers(entry), require_connected=True)
                        if not status.get('active') or status.get('scope') != list(entry['scope'][1:]):
                            self._retire(entry)
                        entry['checked'] = time.monotonic()
                    except (WorkspaceError, OSError):
                        self._retire(entry)
                if entry['closed']:
                    self._cleanup(entry)

    def close(self):
        self.stopped.set()
        self.revoke()
        self.wake.set()
        self.reaper.join(5)

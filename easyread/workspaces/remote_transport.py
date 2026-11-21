"""One strict OpenSSH lifecycle, many bounded HTTP streams over its SOCKS tunnel.

No request is retried: after a lost response a mutation may have completed.
The per-worker bearer secret travels only on SSH stdin and tunneled HTTP, never
in process arguments, the registry, a browser response or an error diagnostic.
"""
from __future__ import annotations

from contextlib import contextmanager
import http.client
import hashlib
from functools import lru_cache
from pathlib import Path
import json
import os
import queue
import secrets
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import re
import threading
import time

from . import WorkspaceError

PROTOCOL = 2
START_TIMEOUT = 25
REQUEST_TIMEOUT = 180
MAX_JSON = 32 * 1024 * 1024


@lru_cache(maxsize=1)
def build_id():
    """Protocol peers must run identical workspace code, not just a release tag."""
    root = Path(__file__).resolve().parent
    if getattr(sys, 'frozen', False):
        try:
            value = (root / 'remote-build.txt').read_text('ascii').strip()
            if re.fullmatch(r'[a-f0-9]{64}', value):
                return value
        except OSError:
            pass
        raise WorkspaceError('This packaged backend lacks its SSH worker fingerprint; rebuild with scripts/build_backend.py', 503, 'remote_protocol')
    digest = hashlib.sha256()
    paths = sorted(root.glob('*.py')) + [root.parent / 'server' / 'workspaces_api.py']
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_text('utf-8').replace('\r\n', '\n').encode('utf-8'))
    return digest.hexdigest()


def unavailable():
    return WorkspaceError('SSH connection unavailable. Verify the known-host key, key authentication, Python and worker installation with the setup script. A lost response may have completed remotely; inspect state before retrying.', 503, 'remote_unavailable')


def ssh_command(config, port):
    ssh = shutil.which('ssh')
    if not ssh:
        raise WorkspaceError('Install the OpenSSH client on the EasyRead server', 503, 'remote_unavailable')
    args = [ssh, '-F', os.devnull, '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'PermitLocalCommand=no',
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=15',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
            '-o', 'LogLevel=ERROR', '-p', str(config['port']), '-l', config['user'],
            '-D', f'127.0.0.1:{port}']
    if config['identity_file']:
        args += ['-o', 'IdentitiesOnly=yes', '-i', config['identity_file']]
    command = shlex.join([config['python'], '-m', 'easyread.workspaces.remote_worker',
                          '--workspace', config['workspace']])
    return args + ['--', config['host'], command]


def _receive(sock, count):
    parts = bytearray()
    while len(parts) < count:
        chunk = sock.recv(count - len(parts))
        if not chunk:
            raise OSError('Tunnel closed')
        parts.extend(chunk)
    return bytes(parts)


class TunnelHTTP(http.client.HTTPConnection):
    def __init__(self, proxy_port, remote_port):
        super().__init__('127.0.0.1', remote_port, timeout=REQUEST_TIMEOUT)
        self.proxy_port = proxy_port

    def connect(self):
        self.sock = socket.create_connection(('127.0.0.1', self.proxy_port), timeout=15)
        try:
            self.sock.sendall(b'\x05\x01\x00')
            if _receive(self.sock, 2) != b'\x05\x00':
                raise OSError('SOCKS authentication failed')
            self.sock.sendall(b'\x05\x01\x00\x01\x7f\x00\x00\x01' + struct.pack('!H', self.port))
            reply = _receive(self.sock, 4)
            if reply[:2] != b'\x05\x00':
                raise OSError('SOCKS connection failed')
            count = {1: 4, 4: 16}.get(reply[3])
            if reply[3] == 3:
                count = _receive(self.sock, 1)[0]
            if count is None:
                raise OSError('Invalid SOCKS address')
            _receive(self.sock, count + 2)
            self.sock.settimeout(REQUEST_TIMEOUT)
        except Exception:
            self.close()
            raise


class Connection:
    def __init__(self, config):
        self.config = config
        self.proc = None
        self.token = secrets.token_urlsafe(32)
        self.remote_port = 0
        self.port = 0
        self.last_used = time.monotonic()
        self.active = 0
        self.lock = threading.RLock()
        self.pool = []
        self._closed = False

    @property
    def connected(self):
        return bool(not self._closed and self.proc and self.proc.poll() is None and self.remote_port)

    def start(self):
        with self.lock:
            if self.connected:
                return
            if self._closed or self.proc is not None:
                # A dead generation is never restarted. The pool creates a new
                # connection, bearer and library tickets for explicit new work.
                raise unavailable()
            with socket.socket() as reserve:
                reserve.bind(('127.0.0.1', 0))
                self.port = reserve.getsockname()[1]
            # Disable config-driven SendEnv and pass no provider keys to ssh.
            env = {k: v for k, v in os.environ.items() if k in
                   ('PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'SystemRoot', 'WINDIR', 'SSH_AUTH_SOCK', 'TMPDIR', 'TEMP', 'TMP')}
            ready = queue.Queue(maxsize=1)
            try:
                self.proc = subprocess.Popen(ssh_command(self.config, self.port), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.proc.stdin.write((json.dumps({'token': self.token, 'protocol': PROTOCOL, 'build': build_id()}) + '\n').encode())
                self.proc.stdin.flush()

                def startup():
                    # Login banners may precede the marker; never accumulate logs.
                    try:
                        budget = 65536
                        while budget > 0:
                            line = self.proc.stdout.readline(min(4096, budget))
                            budget -= len(line)
                            if not line:
                                break
                            if line.startswith(b'EASYREAD_REMOTE '):
                                ready.put(json.loads(line[len(b'EASYREAD_REMOTE '):]))
                                return
                    except (OSError, ValueError):
                        pass
                    ready.put(None)

                def discard(stream):
                    try:
                        while stream.read(4096):
                            pass
                    except (OSError, ValueError):
                        pass

                threading.Thread(target=discard, args=(self.proc.stderr,), daemon=True).start()
                threading.Thread(target=startup, daemon=True).start()
                info = ready.get(timeout=START_TIMEOUT)
                if isinstance(info, dict) and info.get('code') == 'remote_busy':
                    raise WorkspaceError('This remote workspace is owned by another EasyRead worker. Disconnect that client first.', 409, 'remote_busy')
                if not isinstance(info, dict) or info.get('protocol') != PROTOCOL or info.get('build') != build_id() or type(info.get('port')) is not int or not 1 <= info['port'] <= 65535:
                    raise unavailable()
                self.remote_port = info['port']
                threading.Thread(target=discard, args=(self.proc.stdout,), daemon=True).start()
            except WorkspaceError:
                self.close()
                raise
            except (OSError, ValueError, queue.Empty):
                self.close()
                raise unavailable() from None

    @contextmanager
    def request(self, method, path, body=None, headers=None, *, require_connected=False):
        if not require_connected:
            self.start()
        with self.lock:
            if not self.connected:
                raise unavailable()
            self.active += 1
            while self.pool and time.monotonic() - self.pool[-1]._remote_last_used > 60:
                self.pool.pop().close()
            conn = self.pool.pop() if self.pool else TunnelHTTP(self.port, self.remote_port)
        reusable = False
        try:
            outgoing = {'Authorization': 'Bearer ' + self.token, **(headers or {})}
            try:
                conn.request(method, path, body=body, headers=outgoing)
                response = conn.getresponse()
            except (OSError, http.client.HTTPException):
                # Never replay a failed request, even when a stale pooled HTTP
                # connection was the cause. Other requests/tasks retain SSH.
                raise unavailable() from None
            yield response
            reusable = response.isclosed() and not response.will_close
        finally:
            with self.lock:
                self.active -= 1
                self.last_used = time.monotonic()
                if reusable and self.connected and len(self.pool) < 8:
                    conn._remote_last_used = time.monotonic()
                    self.pool.append(conn)
                else:
                    conn.close()

    def json(self, method, path, body=None, *, headers=None, require_connected=False):
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
        with self.request(method, path, raw, {'Content-Type': 'application/json', **(headers or {})},
                          require_connected=require_connected) as response:
            try:
                data = response.read(MAX_JSON + 1)
            except (OSError, http.client.HTTPException):
                raise unavailable() from None
            if len(data) > MAX_JSON:
                raise WorkspaceError('Remote JSON response exceeds safety limit', 502, 'remote_protocol')
            if response.length:
                raise unavailable()
            try:
                result = json.loads(data)
            except ValueError:
                raise WorkspaceError('Invalid remote worker response', 502, 'remote_protocol') from None
            if not isinstance(result, dict):
                raise WorkspaceError('Invalid remote worker response object', 502, 'remote_protocol')
            if response.status >= 400:
                raise WorkspaceError(result.get('error', 'Remote operation failed'), response.status, result.get('code', 'remote_error'))
            return result

    @contextmanager
    def terminal_socket(self):
        # A terminal handle belongs to this exact worker generation. Opening a
        # WebSocket must never spawn another worker or replay a terminal open.
        with self.lock:
            if not self.connected:
                raise unavailable()
            self.active += 1
        conn = TunnelHTTP(self.port, self.remote_port)
        try:
            conn.connect()
            with self.lock:
                if not self.connected:
                    raise unavailable()
            yield conn.sock
        finally:
            conn.close()
            with self.lock:
                self.active -= 1
                self.last_used = time.monotonic()

    def close(self):
        with self.lock:
            self._closed = True
            for conn in self.pool:
                conn.close()
            self.pool.clear()
            proc = self.proc
            if proc:
                try:
                    proc.stdin.close()  # Worker EOF cancels tasks and flushes history.
                except (OSError, ValueError):
                    pass
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=3)
                for stream in (proc.stdout, proc.stderr):
                    stream.close()

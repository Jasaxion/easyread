"""Narrow authenticated workspace worker, installed and launched by the user.

Only loopback is bound. A fresh bearer secret arrives on SSH stdin; EOF shuts
this worker down, cancels its subprocess tasks and preserves session journals.
No deployment HTTP API, library server, settings or unauthenticated RPC exists.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import signal
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, unquote, urlsplit

from filelock import FileLock, Timeout

from . import WorkspaceError, files
from .remote_config import ID
from .remote_environment import capabilities, check
from .remote_terminal_worker import TerminalOwners
from .remote_papers import ReferenceStore
from .remote_transport import PROTOCOL, build_id


class WorkerHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    @property
    def app(self):
        return self.server.app

    def log_message(self, *args):
        pass  # URLs and tokens are never startup diagnostics.

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, status, body):
        self._send(status, json.dumps(body, ensure_ascii=False).encode(), 'application/json; charset=utf-8')

    def _download(self, body, filename, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(filename))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 <= length <= files.MAX_FILE:
            raise WorkspaceError('Upload exceeds 200 MiB', 413)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise WorkspaceError('Incomplete request body')
        return raw

    def do_HEAD(self):
        self._handle()

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _handle(self):
        from ..server.workspaces_api import PREFIX, _body, _dispatch, _route_parts
        self.access_sid = ''
        try:
            tokens = self.headers.get_all('Authorization', [])
            if len(tokens) != 1 or not hmac.compare_digest(tokens[0], 'Bearer ' + self.server.token):
                self.close_connection = True
                return self._json(401, {'error': 'Unauthorized'})
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) > 1:
                raise WorkspaceError('Invalid request framing')
            length = self.headers.get('Content-Length', '0')
            if not length.isascii() or not length.isdigit() or len(length) > 12 or int(length) > files.MAX_FILE:
                raise WorkspaceError('Invalid request length', 413)
            url = urlsplit(self.path)
            path = unquote(url.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            get = self.command in ('GET', 'HEAD')
            if path.startswith('/workspace-terminal/'):
                from .remote_terminal_relay import PATH
                if not get or not PATH.fullmatch(self.path):
                    raise WorkspaceError('Invalid terminal route', 403)
                return self.app.access.relay(self)
            if path == '/status' and get:
                return self._json(200, {'busy': self.server.allocating() or self.app.workspaces.busy(), 'protocol': PROTOCOL})
            if path == '/check' and self.command == 'POST':
                return self._json(200, check(self.app.lib.root, _body(self).get('engine', 'codex')))
            parts = _route_parts(path)
            project_route = path.startswith(PREFIX + '/') and parts and ID.fullmatch(parts[0])
            if (project_route and len(parts) == 5 and parts[1] == 'sessions'
                    and parts[3:] == ['terminal', 'close'] and self.command == 'POST'):
                # Owner-scoped tombstones must work even after a project was
                # deleted or an open failed before the project could be read.
                owner = self.app.access.token(self.headers)
                sessions = self.app.workspaces.sessions
                sessions._check_sid(parts[2])
                _body(self)
                return self._json(200, self.app.access.close(sessions, parts[0], parts[2], owner))
            if not self.server.workspace_valid:
                raise WorkspaceError('Remote workspace must exist, be writable and have no symlink ancestors; run Check for diagnostics', 409, 'remote_environment')
            if path == PREFIX + '/create' and self.command == 'POST':
                body = _body(self)
                if not ID.fullmatch(str(body.get('_allocation_id', ''))):
                    raise WorkspaceError('A client allocation ID is required')
                with self.server.allocation(body['_allocation_id']):
                    result = check(self.app.lib.root, body.get('engine', 'codex'))
                    if not result['ok']:
                        return self._json(409, {'error': 'Remote environment is not ready', 'code': 'remote_environment', **result})
                    result = self.app.workspaces.store.create(body)
                    project = result['project']
                    if not all(project['initialization'].get(key, {}).get('ok') for key in ('git', 'beads')):
                        self.app.workspaces.store.delete(project['id'])
                        raise WorkspaceError('Git or Beads initialization failed; allocation removed. Run the remote check and initialize Git/Beads credentials on the host.', 409, 'remote_environment')
                    return self._json(200, result)
            if not project_route:
                raise WorkspaceError('Route not found', 404)
            pid = parts[0]
            if self.server.allocating(pid):
                raise WorkspaceError('Project initialization is still running', 409, 'remote_busy')
            store = self.app.workspaces.store
            project = store.get(pid)
            if len(parts) in (4, 5) and parts[1] == 'sessions' and parts[3] == 'terminal':
                owner = self.app.access.token(self.headers)
                sessions = self.app.workspaces.sessions
                sessions._check_sid(parts[2])
                if len(parts) == 4 and self.command == 'POST':
                    _body(self)
                    return self._json(200, self.app.access.open(sessions, pid, parts[2], owner))
                if len(parts) == 5 and parts[4] == 'status' and get:
                    return self._json(200, self.app.access.status(sessions, pid, parts[2], owner))
                raise WorkspaceError('Route not found', 404)
            if len(parts) == 4 and parts[1] == 'sessions' and parts[3] in ('cancel', 'delete'):
                sessions = self.app.workspaces.sessions
                # Deletion calls cancel too. Both require the same ownership
                # check under the native session-operation reservation.
                with sessions.operation(pid, parts[2]):
                    self.app.access.guard_cancel(sessions, pid, parts[2])
                    return _dispatch(self, path, query)
            if len(parts) == 1 and get:
                # A malformed agent-edited manifest must not lock the user out
                # of the file editor. The paper route reports the actual error.
                try:
                    project = store.describe(pid)
                except WorkspaceError as exc:
                    project = {**project, 'paper_links_error': str(exc)}
                return self._json(200, {'project': project,
                    'workspace_root': str(store.root(pid)), 'capabilities': capabilities()})
            _dispatch(self, path, query)
        except (WorkspaceError, ValueError, TypeError, KeyError) as exc:
            self.close_connection = True
            self._json(getattr(exc, 'status', 400), {'error': str(exc), 'code': getattr(exc, 'code', 'invalid_request')})
        except FileNotFoundError:
            self.close_connection = True
            self._json(404, {'error': 'File not found'})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            self._json(500, {'error': 'Remote worker operation failed', 'code': 'remote_error'})


class WorkerServer(ThreadingHTTPServer):
    daemon_threads = False
    request_queue_size = 32

    def __init__(self, workspace, token):
        from ..server.workspaces_api import Workspaces
        super().__init__(('127.0.0.1', 0), WorkerHandler)
        self.token = token
        root = Path(workspace)
        self.workspace_valid = (root.is_absolute() and root != Path('/') and root.is_dir()
                                and os.access(root, os.W_OK | os.X_OK)
                                and not any(p.is_symlink() for p in (root, *root.parents)))
        self.slots = threading.BoundedSemaphore(32)
        self.clients = set()
        self.clients_lock = threading.Lock()
        self.allocations = set()
        self.allocations_lock = threading.Lock()
        self.app = SimpleNamespace(lib=SimpleNamespace(root=Path(workspace)), access=TerminalOwners(),
                                   token='', remote_worker=True, workspaces=None)
        self.app.workspaces = Workspaces(self.app, store=ReferenceStore(workspace))

    def allocating(self, pid=None):
        with self.allocations_lock:
            return bool(self.allocations) if pid is None else pid in self.allocations

    @contextmanager
    def allocation(self, pid):
        with self.allocations_lock:
            if pid in self.allocations:
                raise WorkspaceError('Project initialization is still running', 409, 'remote_busy')
            self.allocations.add(pid)
        try:
            yield
        finally:
            with self.allocations_lock:
                self.allocations.remove(pid)

    def get_request(self):
        sock, addr = super().get_request()
        sock.settimeout(180)
        return sock, addr

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self.clients_lock:
            self.clients.add(request)
        try:
            super().process_request(request, address)
        except Exception:
            with self.clients_lock:
                self.clients.discard(request)
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            with self.clients_lock:
                self.clients.discard(request)
            self.slots.release()

    def close_clients(self):
        with self.clients_lock:
            for client in self.clients:
                try:
                    client.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--check', action='store_true', help='Print readiness checks; never install or create projects')
    parser.add_argument('--engine', choices=('codex', 'claude', 'pi'), default='codex')
    args = parser.parse_args(argv)
    if args.check:
        result = check(args.workspace, args.engine)
        print(json.dumps(result, indent=2))
        return 0 if result['ok'] else 1
    raw = sys.stdin.buffer.readline(4097)
    try:
        auth = json.loads(raw)
        token = auth['token']
        if len(raw) > 4096 or auth['protocol'] != PROTOCOL or auth.get('build') != build_id() or not isinstance(token, str) or not 32 <= len(token) <= 128 or not token.isascii():
            return 2
    except (ValueError, KeyError, TypeError):
        return 2
    # A separate transport must not race two native runtime owners on one store.
    root = Path(args.workspace)
    if not root.is_absolute() or not root.is_dir() or root == Path('/') or not os.access(root, os.W_OK | os.X_OK) or any(p.is_symlink() for p in (root, *root.parents)):
        # Allow /check diagnostics without creating anything at an invalid root.
        root_lock = None
    else:
        root_lock = FileLock(str(root / '.easyread-remote-worker.lock'), timeout=0)
    server = None
    try:
        if root_lock:
            root_lock.acquire()
        server = WorkerServer(args.workspace, token)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stopped = threading.Event()
        def input_closed():
            try:
                while os.read(sys.stdin.fileno(), 1):
                    pass
            finally:
                stopped.set()
        # SSH can deliver HUP/TERM before stdin EOF. Keep ownership until
        # admitted operations finish and Agent/LaTeX children have been stopped.
        for name in ('SIGTERM', 'SIGHUP', 'SIGINT'):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: stopped.set())
        threading.Thread(target=input_closed, daemon=True).start()
        print('EASYREAD_REMOTE ' + json.dumps({'port': server.server_port, 'protocol': PROTOCOL, 'build': build_id()}), flush=True)
        stopped.wait()
    except Timeout:
        print('EASYREAD_REMOTE ' + json.dumps({'code': 'remote_busy'}), flush=True)
        return 3
    finally:
        try:
            if server:
                server.shutdown()
                server.close_clients()
                server.server_close()  # Finish admitted work before closing sessions.
                server.app.workspaces.close()
        finally:
            if root_lock and root_lock.is_locked:
                root_lock.release()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

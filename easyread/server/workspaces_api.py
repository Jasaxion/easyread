"""Authenticated HTTP adapter for independent research projects."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..app import config
from ..workspaces import WorkspaceError, files, git, archives
from ..workspaces.store import Store, capabilities
from . import pwa

PREFIX = '/api/workspaces'
_INIT_LOCK = threading.RLock()


class Workspaces:
    def __init__(self, app):
        from ..workspaces.sessions import Sessions
        self.store = Store(app.lib.root)
        self.sessions = Sessions(self.store)
        self.sessions.attach_access(app.access)

    def busy(self, pid=None):
        from ..workspaces import latex
        return self.sessions.busy(pid) or latex.busy(self.store.root(pid) if pid else None)

    def idle(self, pid):
        if self.busy(pid):
            raise WorkspaceError('Stop running AI/LaTeX tasks before this operation', 409, 'project_busy')

    def close(self):
        from ..workspaces import latex
        self.sessions.close()
        if hasattr(latex, 'close'):
            latex.close()


def manager(app):
    with _INIT_LOCK:
        if getattr(app, 'workspaces', None) is None:
            app.workspaces = Workspaces(app)
        return app.workspaces


def busy(app):
    current = getattr(app, 'workspaces', None)
    return bool(current and current.busy())


def revoke(app, access_sid=None):
    current = getattr(app, "workspaces", None)
    if current:
        current.sessions.terminals.close(access_sid)


def close(app):
    current = getattr(app, 'workspaces', None)
    if current:
        current.close()


def handle(handler):
    url = urlsplit(handler.path)
    path = unquote(url.path)
    if path == '/workspaces' and handler.command in ('GET', 'HEAD'):
        handler._page(config.WEB / 'workspaces.html')
        return True
    if not (path == PREFIX or path.startswith(PREFIX + '/') or path.startswith('/workspace-terminal/')):
        return False
    try:
        _dispatch(handler, path, {k: v[0] for k, v in parse_qs(url.query).items()})
    except WorkspaceError as exc:
        handler._json(exc.status, {'error': str(exc), 'code': exc.code})
    except (ValueError, TypeError, KeyError) as exc:
        handler._json(getattr(exc, 'status', 400), {'error': str(exc), 'code': getattr(exc, 'code', 'invalid_request')})
    except FileNotFoundError:
        handler._json(404, {'error': 'File not found'})
    return True


def _dispatch(h, path, query):
    work = manager(h.app)
    store, sessions = work.store, work.sessions
    get = h.command in ('GET', 'HEAD')
    if path.startswith('/workspace-terminal/'):
        if not get:
            raise WorkspaceError('Method not allowed', 405)
        return sessions.relay(h, h.access_sid)
    parts = path[len(PREFIX):].strip('/').split('/') if path != PREFIX else []
    if get and not parts:
        from ..workspaces import latex
        native = sessions.capabilities()
        return h._json(200, {'projects': store.listing(query.get('q', '')), 'token': h.app.token,
                             'library_id': pwa.library_id(h.app.lib),
                             'capabilities': {**capabilities(), 'latex': latex.capabilities(),
                                 'terminal': {'available': native['terminal'], 'reason': native['terminal_reason']}}})
    if not get and parts == ['create']:
        return h._json(200, store.create(_body(h)))
    if not get and parts == ['import']:
        return h._json(200, archives.import_project(store, sessions, h._body()))
    if not parts:
        raise WorkspaceError('Route not found', 404)
    pid = parts[0]
    root = store.root(pid)
    action = '/'.join(parts[1:])
    if get:
        if not action:
            result = {'project': store.get(pid), 'workspace_root': str(root)}
        elif action == 'tree':
            result = files.tree(root, query.get('path', ''))
        elif action == 'file':
            result = files.read(root, query.get('path', ''))
        elif action == 'raw':
            return _raw(h, files.target(root, query.get('path', ''), missing=False))
        elif action == 'papers':
            result = store.papers(pid, query.get('q', ''))
        elif action == 'models':
            from ..workspaces import model_catalog
            if query.get('refresh', '0') not in ('0', '1'):
                raise WorkspaceError('refresh must be 0 or 1')
            result = model_catalog.listing(store, pid, query.get('engine'), refresh=query.get('refresh') == '1')
        elif action == 'sessions':
            result = sessions.listing(pid)
        elif len(parts) == 3 and parts[1] == 'sessions':
            result = sessions.get(pid, parts[2])
        elif len(parts) == 4 and parts[1] == 'sessions' and parts[3] == 'updates':
            result = sessions.updates(pid, parts[2], query.get('after', 0), query.get('limit', 100))
        elif len(parts) == 4 and parts[1] == 'sessions' and parts[3] == 'history':
            result = sessions.history(pid, parts[2], query.get('before'), query.get('limit', 40))
        elif action in ('beads', 'beads/show'):
            from ..workspaces import beads
            with store.lock(pid):
                result = beads.listing(root) if action == 'beads' else beads.show(root, query.get('id'))
        elif action == 'git':
            with store.lock(pid):
                work.idle(pid)
                result = git.status(root)
        elif action == 'export':
            sessions.prepare_export(pid)
            with store.lock(pid):
                work.idle(pid)
                data = archives.export_project(store, sessions, pid)
            return h._download(data, pid + '.easyread.zip', 'application/zip')
        elif action == 'latex':
            from ..workspaces import latex
            with store.lock(pid):
                result = latex.initialize(root)
        else:
            raise WorkspaceError('Route not found', 404)
    else:
        if action == 'latex/import-zip':
            from ..workspaces import latex_import
            if query.get('replace', '0') not in ('0', '1'):
                raise WorkspaceError('replace must be 0 or 1')
            length = int(h.headers.get('Content-Length', '0'))
            if not 0 < length <= files.MAX_FILE:
                raise WorkspaceError('ZIP upload must be between 1 byte and 200 MiB', 413)
            raw = h._body()
            with store.lock(pid):
                work.idle(pid)
                result = latex_import.import_zip(root, raw, replace=query.get('replace') == '1')
        elif action == 'files/upload':
            with store.lock(pid):
                result = files.upload(root, query.get('path', ''), h._body())
        else:
            body = _body(h)
            if action == 'update':
                result = store.update(pid, body)
            elif action == 'delete':
                sessions.prepare_export(pid)
                with store.lock(pid):
                    work.idle(pid)
                    sessions.require_sleeping(pid)
                    result = store.delete(pid)
            elif action.startswith('files/'):
                with store.lock(pid):
                    file = body.get('path', '')
                    if action == 'files/create':
                        result = files.create(root, file, body.get('kind', 'file'), body.get('content', ''))
                    elif action == 'files/write':
                        result = files.write(root, file, body.get('content'), body.get('version'))
                    elif action == 'files/move':
                        result = files.move(root, file, body.get('destination', ''))
                    elif action == 'files/delete':
                        result = files.delete(root, file)
                    else:
                        raise WorkspaceError('Route not found', 404)
            elif action.startswith('beads/'):
                from ..workspaces import beads
                with store.lock(pid):
                    result = beads.action(root, action.removeprefix('beads/'), body)
            elif action in ('papers/link', 'papers/unlink'):
                result = store.link(pid, body.get('document_id'), action.endswith('/unlink'))
            elif action == 'sessions/create':
                result = sessions.create(pid, body)
            elif len(parts) == 4 and parts[1] == 'sessions':
                sid, operation = parts[2:]
                if operation == 'send':
                    result = sessions.send(pid, sid, body)
                elif operation == 'cancel':
                    result = sessions.cancel(pid, sid)
                elif operation == 'goal':
                    result = sessions.goal(pid, sid, body)
                elif operation == 'steer':
                    result = sessions.steer(pid, sid, body)
                elif operation == 'respond':
                    result = sessions.respond(pid, sid, body)
                elif operation == 'settings':
                    result = sessions.settings(pid, sid, body)
                elif operation == 'rename':
                    result = sessions.rename(pid, sid, body)
                elif operation == 'delete':
                    result = sessions.delete(pid, sid)
                elif operation == 'terminal':
                    result = sessions.terminal(pid, sid, h.app.access, h.access_sid)
                else:
                    raise WorkspaceError('Route not found', 404)
            elif action == 'git':
                with store.lock(pid):
                    work.idle(pid)
                    result = git.action(root, body)
            elif action in ('latex/forward-sync', 'latex/inverse-sync'):
                from ..workspaces import latex_sync
                with store.lock(pid):
                    result = latex_sync.forward(root, body) if action.endswith('/forward-sync') else latex_sync.inverse(root, body)
            elif action in ('latex/settings', 'latex/compile', 'latex/cancel'):
                from ..workspaces import latex
                with store.lock(pid):
                    if action.endswith('/cancel'):
                        result = latex.cancel(root)
                    else:
                        result = latex.settings(root, body) if action.endswith('/settings') else latex.start(root, body)
            else:
                raise WorkspaceError('Route not found', 404)
    h._json(200, result)


def _raw(h, path):
    """Serve one range from a pinned, nofollow regular file, never buffer the PDF."""
    with files.open_regular(path) as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > files.MAX_FILE:
            raise WorkspaceError('File too large to preview', 413)
        start, end, status = 0, size - 1, 200
        ranges = h.headers.get_all('Range', [])
        if ranges:
            match = re.fullmatch(r'bytes=([0-9]*)-([0-9]*)', ranges[0].strip(), re.I) if len(ranges) == 1 else None
            valid = bool(match and any(match.groups()) and size)
            if valid:
                first, last = match.groups()
                # Bound parsing of maliciously long decimal strings as well.
                valid = len(first) <= 20 and len(last) <= 20
                if valid:
                    if first:
                        start = int(first)
                        end = min(int(last), size - 1) if last else size - 1
                    else:
                        suffix = int(last)
                        start = max(0, size - suffix)
                    valid = 0 <= start <= end < size
            if not valid:
                h.send_response(416)
                h.send_header('Content-Range', f'bytes */{size}')
                h.send_header('Accept-Ranges', 'bytes')
                h.send_header('Content-Length', '0')
                h.send_header('Cache-Control', 'no-store')
                h.end_headers()
                return
            status = 206
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        inline = mime in ('application/pdf', 'image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp')
        h.send_response(status)
        h.send_header('Content-Type', mime if inline else 'application/octet-stream')
        if not inline:
            h.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(path.name))
        h.send_header('Content-Length', str(max(0, end - start + 1)))
        h.send_header('Accept-Ranges', 'bytes')
        if status == 206:
            h.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        h.send_header('Content-Security-Policy', "sandbox; default-src 'none'")
        h.send_header('X-Content-Type-Options', 'nosniff')
        h.send_header('Cache-Control', 'no-store')
        h.end_headers()
        if h.command == 'HEAD':
            return
        stream.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                h.close_connection = True
                break
            try:
                h.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return
            remaining -= len(chunk)


def _body(handler):
    raw = handler._body()
    if len(raw) > files.MAX_TEXT + 65536:
        raise WorkspaceError('JSON request too large', 413)
    data = json.loads(raw or b'{}')
    if not isinstance(data, dict):
        raise WorkspaceError('Expected a JSON object')
    return data

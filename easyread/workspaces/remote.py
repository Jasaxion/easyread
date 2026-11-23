"""Local remote-workspace coordinator: offline references, admission and proxying."""
from __future__ import annotations

import hashlib
import json
import http.client
import os
from urllib.parse import quote, urlsplit

from ..library.store import now_iso
from . import WorkspaceError, files
from .remote_config import identifier
from .remote_servers import RemoteServers
from .remote_transport import Connection
from .store import Store

PREFIX = '/api/workspaces'


class _LimitedBody:
    def __init__(self, stream, length):
        self.stream, self.remaining = stream, length

    def read(self, size=-1):
        if not self.remaining:
            return b''
        data = self.stream.read(min(self.remaining, 65536 if size < 0 else size))
        if not data:
            raise OSError('Incomplete upload')
        self.remaining -= len(data)
        return data


class Remotes(RemoteServers):
    def __init__(self, libroot, access=None):
        super().__init__(libroot)
        from .remote_terminal import RemoteTerminals
        self.terminals = RemoteTerminals(self, access)

    def close(self):
        try:
            self.terminals.close()
        finally:
            super().close()
            # Dead workers no longer need pending-cleanup reservations.
            self.terminals.revoke()

    def _new_connection(self, config):
        return Connection(config)

    @staticmethod
    def decorate(project, config, *, pending=False):
        return {**project, 'remote': {'server_id': config['id'], 'server_name': config['name'],
                                    'workspace_root': config['workspace'] + '/workspace/' + project['id'],
                                    **({'pending': True} if pending else {})}}

    def create(self, body):
        name, description = Store.validate(body)
        if body.get('engine', 'codex') not in ('codex', 'claude', 'pi'):
            raise WorkspaceError('Choose Codex, Claude Code or Pi Agent')
        sid = body.get('server_id')
        # The per-server reservation prevents retarget/delete, while the lease
        # protects SSH ownership. Neither blocks requests to existing projects.
        with self.control(sid), self.connection(sid) as conn:
            check = conn.json('POST', '/check', {'engine': body.get('engine', 'codex')})
            with self.registry.transaction() as data:
                config = self.registry.server(sid, data)
                data['servers'][sid]['last_check'] = check
                if check['ok']:
                    pid = identifier()
                    while pid in data['projects'] or (Store(self.libroot).base / pid).exists():
                        pid = identifier()
                    project = {'id': pid, 'name': name, 'description': description, 'engine': body.get('engine', 'codex'),
                               'created': now_iso(), 'updated': now_iso(), 'papers': [], 'initialization': {}}
                    # Persist intent before the remote mutation; a lost response
                    # never loses the fixed host/id and is never retried.
                    data['projects'][pid] = self.decorate(project, config, pending=True)
            if not check['ok']:
                raise WorkspaceError('Remote environment is not ready; run the server check for diagnostics', 409, 'remote_environment')
            try:
                result = conn.json('POST', PREFIX + '/create', {**project, '_allocation_id': pid})
            except WorkspaceError as exc:
                if exc.code == 'remote_environment':
                    with self.registry.transaction() as data:
                        data['projects'].pop(pid, None)
                raise
            return self._cache(sid, result)

    def _cache(self, sid, result):
        if not isinstance(result, dict) or not isinstance(result.get('project'), dict):
            return result
        from .remote_terminal import enabled
        if not enabled() and isinstance(result.get('capabilities'), dict):
            result['capabilities']['terminal'] = {
                'available': False, 'reason': 'Enable EASYREAD_TERMINAL=1 on the EasyRead deployment to allow remote terminals'}
        with self.registry.locked() as data:
            project = result['project']
            existing = data['projects'].get(project.get('id'))
            if not existing or existing['remote']['server_id'] != sid:
                raise WorkspaceError('Remote project identity mismatch', 502, 'remote_protocol')
            result['project'] = self.decorate(project, self.registry.server(sid, data))
            if existing != result['project']:
                data['projects'][project['id']] = result['project']
                self.registry.write(data)
        return result

    def papers(self, project, query):
        # Candidate browsing stays local; an explicit link transfers IDs only.
        from ..library.library import Library
        from ..library.summary_cache import summary_version
        if query.get('linked_only', '0') not in ('0', '1'):
            raise WorkspaceError('linked_only must be 0 or 1')
        lib = Library(self.libroot)
        ids = project.get('papers', [])
        linked, available = [], []
        selected = [(i, lib.ws(i)) for i in ids]
        version = None
        if query.get('linked_only') == '1' and os.name != 'nt':
            # Hash the ordered references AND local summary inputs. An Agent edit,
            # rename, library deletion or later reimport must invalidate the view.
            stamps = [(i, summary_version(ws) if ws else None) for i, ws in selected]
            version = hashlib.sha256(json.dumps(stamps).encode()).hexdigest()
            if query.get('if_version') == version:
                return {'unchanged': True, 'version': version}
        for document_id, ws in selected:
            linked.append(lib.summary(ws, repair=False) if ws else {'id': document_id, 'display_title': document_id, 'missing': True})
        if query.get('linked_only') != '1':
            q = query.get('q', '').casefold()
            for ws in lib.all():
                if ws.id not in ids:
                    summary = lib.summary(ws, repair=False)
                    if q in json.dumps(summary, ensure_ascii=False).casefold():
                        available.append(summary)
        return {'linked': linked, 'available': available[:200], 'mode': 'native_api', **({'version': version} if version else {})}

    def dispatch(self, h, pid, action, query):
        from ..server.workspaces_api import _body
        project = self.registry.project(pid)
        sid = project['remote']['server_id']
        if project['remote'].get('pending'):
            # Pending intent is visible offline, but cannot be forgotten or
            # mutated while its creation still owns the server reservation.
            with self.control(sid):
                pass
        parts = action.split('/')
        if len(parts) >= 3 and parts[0] == 'sessions' and parts[2] == 'terminal':
            # Worker owner headers and control routes are private, never exposed
            # through the generic workspace forwarding path.
            if len(parts) != 3 or h.command != 'POST':
                raise WorkspaceError('Route not found', 404)
            _body(h)
            return h._json(200, self.terminals.open(sid, pid, parts[1], h.access_sid))
        if len(parts) == 3 and parts[0] == 'sessions' and parts[2] == 'cancel' and h.command == 'POST':
            body = _body(h)
            closed = self.terminals.close_scope(sid, pid, parts[1], h.access_sid)
            target = quote(PREFIX + '/' + pid + '/sessions/' + parts[1], safe='/')
            with self.connection(sid) as conn:
                result = conn.json('GET', target) if closed else conn.json('POST', target + '/cancel', body)
                return h._json(200, result)
        with self.connection(sid) as conn:
            if h.command in ('GET', 'HEAD') and action == 'papers':
                # Ask for references only; don't repeat the project/CLI capabilities
                # lookup each time the sidebar polls. Titles stay on this deployment.
                result = self._cache(sid, conn.json('GET', PREFIX + '/' + pid + '/papers?linked_only=1'))
                return h._json(200, self.papers(result['project'], query))
            if h.command in ('GET', 'HEAD') and not action:
                result = self._cache(sid, conn.json('GET', PREFIX + '/' + pid))
                return h._json(200, result)
            if h.command == 'POST' and action in ('update', 'delete', 'papers/link', 'papers/unlink'):
                body = _body(h)
                if action in ('papers/link', 'papers/unlink'):
                    from ..library.library import Library
                    document_id = body.get('document_id')
                    if not isinstance(document_id, str) or not document_id or len(document_id) > 200:
                        raise WorkspaceError('Invalid document ID')
                    if action == 'papers/link' and Library(self.libroot).ws(document_id) is None:
                        raise WorkspaceError('Document not found', 404)
                    result = conn.json('POST', PREFIX + '/' + pid + '/' + action,
                                       {'document_id': document_id})
                else:
                    # Delete only the registry's fixed host/id. No request or archive
                    # may supply a different target. Worker confirms the project exists.
                    if action == 'delete':
                        try:
                            conn.json('GET', PREFIX + '/' + pid)
                        except WorkspaceError as exc:
                            if exc.status != 404:
                                raise
                            # Explicit delete after an authoritative missing-project
                            # response only forgets the stale reference, not files.
                            result = {'ok': True, 'missing': True}
                        else:
                            result = conn.json('POST', PREFIX + '/' + pid + '/delete', body)
                    else:
                        result = conn.json('POST', PREFIX + '/' + pid + '/' + action, body)
                if action == 'delete':
                    with self.registry.transaction() as data:
                        data['projects'].pop(pid, None)
                else:
                    result = self._cache(sid, result)
                return h._json(200, result)
            length_text = h.headers.get('Content-Length', '0')
            if (h.headers.get('Transfer-Encoding') or len(h.headers.get_all('Content-Length', [])) > 1
                    or not length_text.isascii() or not length_text.isdigit() or len(length_text) > 12
                    or not 0 <= int(length_text) <= files.MAX_FILE):
                h.close_connection = True
                raise WorkspaceError('Invalid or oversized upload', 413)
            length = int(length_text)
            headers = {'Content-Length': str(length), 'Content-Type': h.headers.get('Content-Type', 'application/octet-stream')}
            ranges = h.headers.get_all('Range', [])
            if ranges:
                headers['Range'] = ','.join(ranges)
            path = urlsplit(h.path)
            # The deployment already decoded this route. Re-encode it for the
            # worker so its one decoding pass cannot change route structure.
            target = quote(PREFIX + '/' + pid + ('/' + action if action else ''), safe='/')
            target += '?' + path.query if path.query else ''
            with conn.request(h.command, target, _LimitedBody(h.rfile, length) if length else None, headers) as response:
                h.send_response(response.status)
                allowed = {'content-type', 'content-length', 'content-range', 'accept-ranges', 'content-disposition',
                           'content-security-policy', 'x-content-type-options', 'cache-control'}
                for name, value in response.getheaders():
                    if name.lower() in allowed:
                        h.send_header(name, value)
                if response.getheader('Content-Length') is None:
                    h.close_connection = True
                    h.send_header('Connection', 'close')
                h.end_headers()
                try:
                    if h.command != 'HEAD':
                        while True:
                            chunk = response.read(65536)
                            if not chunk:
                                if response.length:
                                    response.will_close = True
                                    h.close_connection = True
                                break
                            h.wfile.write(chunk)
                    else:
                        response.read()
                except (OSError, http.client.HTTPException):
                    # Headers may already be on the wire; never append a JSON
                    # error to a partial PDF/upload response or kill active AI.
                    response.will_close = True
                    h.close_connection = True

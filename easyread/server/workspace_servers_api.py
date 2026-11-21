"""SSH server configuration routes, kept separate from project execution."""
from __future__ import annotations

from ..workspaces import WorkspaceError


def dispatch(h, remotes, parts):
    from .workspaces_api import _body
    get = h.command in ('GET', 'HEAD')
    if parts == ['servers'] and get:
        return h._json(200, remotes.servers())
    if parts == ['servers', 'setup-script'] and get:
        from ..workspaces.remote_setup import SCRIPT
        if h.command == 'HEAD':
            return h._send(200, SCRIPT.encode(), 'text/x-shellscript; charset=utf-8')
        return h._download(SCRIPT.encode(), 'easyread-remote-setup.sh', 'text/x-shellscript; charset=utf-8')
    if h.command != 'POST':
        raise WorkspaceError('Route not found', 404)
    body = _body(h)
    if parts == ['servers', 'create']:
        return h._json(200, remotes.save_server(body))
    if len(parts) != 3:
        raise WorkspaceError('Route not found', 404)
    sid, action = parts[1:]
    if action == 'update':
        result = remotes.save_server(body, sid)
    elif action == 'delete':
        result = remotes.delete_server(sid)
    elif action == 'check':
        result = remotes.check(sid, body.get('engine', 'codex'))
    elif action == 'connect':
        result = remotes.connect(sid)
    elif action == 'disconnect':
        result = remotes.disconnect(sid)
    else:
        raise WorkspaceError('Route not found', 404)
    return h._json(200, result)

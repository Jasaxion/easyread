"""Same-origin HTTP/WS relay to the worker pinned by a terminal reservation."""
from __future__ import annotations

import http.client
import re

from ..server.terminal import relay_stream, relay_response, relay_headers
from .remote_terminal_worker import OWNER_HEADER, denied

# ttyd assets only. No query argv, escaping, dot segments, alternate authority
# or worker API can be smuggled through a terminal handle.
PATH = re.compile(r'/workspace-terminal/[a-f0-9]{32}/(?:[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?/?)?')


def relay(manager, handler, access_sid):
    path = handler.path
    if handler.command not in ('GET', 'HEAD') or not PATH.fullmatch(path):
        raise denied()
    try:
        headers = relay_headers(handler)
    except ValueError as exc:
        from . import WorkspaceError
        raise WorkspaceError(str(exc), 403, 'remote_terminal_forbidden') from None
    prefix = '/'.join(path.split('/')[:3]) + '/'
    with manager.lock:
        entry = manager.handles.get(prefix)
        if not entry or entry['access_sid'] != access_sid or not manager.valid(entry):
            raise denied()
    headers[OWNER_HEADER] = entry['owner']
    conn = entry['conn']
    upgrade = headers.get('Upgrade', '').lower() == 'websocket'
    if not upgrade:
        with manager.lock:
            if not manager.valid(entry):
                raise denied()
            entry['sockets'].add(handler.connection)
        try:
            with conn.request(handler.command, path, headers=headers, require_connected=True) as response:
                if not manager.valid(entry):
                    raise denied()
                relay_response(handler, response)
        finally:
            with manager.lock:
                entry['sockets'].discard(handler.connection)
        return
    client = handler.connection
    with manager.lock:
        if entry.get('attached'):
            raise denied()
        # Only one socket may attach; a second request must not stop the first.
        entry['attached'] = True
    try:
        with conn.terminal_socket() as upstream:
            with manager.lock:
                if not manager.valid(entry):
                    raise denied()
                entry['sockets'].update((upstream, client))
            headers['Authorization'] = 'Bearer ' + conn.token
            request = f'GET {path} HTTP/1.1\r\n' + ''.join(f'{name}: {value}\r\n' for name, value in headers.items()) + '\r\n'
            upstream.sendall(request.encode('latin-1'))
            handler.close_connection = True
            # Handshake (including tty subprotocol), resize and input/control
            # frames are opaque. At most 64 KiB is pending in either direction.
            relay_stream(client, upstream, lambda: manager.valid(entry))
    except (OSError, http.client.HTTPException):
        handler.close_connection = True
    finally:
        manager._retire(entry)
        with manager.lock:
            entry['sockets'].clear()
        manager._cleanup(entry)

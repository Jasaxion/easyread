"""Worker-only terminal ownership, separate from browser access credentials.

A token is a single open reservation, not permission to spawn on WS attach.
Revoked reservations remain tombstoned for this worker generation, including
closes received before a delayed open. Nothing here is persisted or exported.
"""
from __future__ import annotations

import re
import threading

from . import WorkspaceError, session_terminal

OWNER_HEADER = 'X-EasyRead-Terminal-Owner'
TOKEN = re.compile(r'[a-f0-9]{64}')
URL = re.compile(r'/workspace-terminal/([a-f0-9]{32})/')
MAX_RESERVATIONS = 4096


def denied():
    return WorkspaceError('Terminal ownership is missing, expired or mismatched', 403, 'remote_terminal_closed')


class TerminalOwners:
    enabled = True

    def __init__(self):
        self.lock = threading.RLock()
        self.reservations = {}

    def valid(self, token):
        with self.lock:
            entry = self.reservations.get(token)
            return bool(entry and not entry['closed'] and session_terminal.enabled())

    @staticmethod
    def token(headers):
        values = headers.get_all(OWNER_HEADER, [])
        if len(values) != 1 or not TOKEN.fullmatch(values[0]):
            raise denied()
        return values[0]

    def reserve(self, token, scope, *, close=False):
        with self.lock:
            entry = self.reservations.get(token)
            if entry:
                if entry['scope'] != scope or (entry['closed'] and not close):
                    raise denied()
            else:
                if len(self.reservations) >= MAX_RESERVATIONS:
                    raise WorkspaceError('Terminal reservation limit reached; reconnect the idle worker', 429, 'remote_terminal_capacity')
                entry = self.reservations[token] = {'scope': scope, 'closed': False, 'opening': False, 'opened': False}
            if close:
                entry['closed'] = True
            return entry

    def open(self, sessions, pid, sid, token):
        entry = self.reserve(token, (pid, sid))
        with self.lock:
            if entry['opening']:
                raise WorkspaceError('Terminal is still opening', 409, 'remote_busy')
            opened = entry['opened']
            entry['opening'] = True
        try:
            if opened:
                url = sessions.terminals.scope_url(pid, sid, token)
                if not url:
                    raise denied()
                return {**entry['result'], 'url': url}
            result = sessions.terminal(pid, sid, self, token)
            with self.lock:
                if not self.valid(token):
                    raise denied()
                result = {key: result[key] for key in ('url', 'engine', 'native_id') if key in result}
                result['scope'] = [pid, sid]
                if not URL.fullmatch(result.get('url', '')):
                    raise denied()
                entry.update(opened=True, result=result)
                return result
        except Exception:
            self.close(sessions, pid, sid, token)
            raise
        finally:
            with self.lock:
                entry['opening'] = False

    def close(self, sessions, pid, sid, token):
        with self.lock:
            # Saturation is permanent for this generation: an unknown token
            # cannot have opened and cannot open later. Acknowledge cleanup
            # rather than retaining an unreleaseable deployment-side lease.
            if token not in self.reservations and len(self.reservations) >= MAX_RESERVATIONS:
                return {'ok': True}
        self.reserve(token, (pid, sid), close=True)
        # Never cancel a GUI turn or a later terminal belonging to another token.
        sessions.terminals.close(token)
        return {'ok': True}

    def status(self, sessions, pid, sid, token):
        with self.lock:
            entry = self.reservations.get(token)
            if not entry or entry['scope'] != (pid, sid):
                raise denied()
            closed, opening = entry['closed'], entry['opening']
        active = not closed and (opening or bool(sessions.terminals.scope_url(pid, sid, token)))
        return {'active': active, 'scope': [pid, sid]}

    def relay(self, handler):
        token = self.token(handler.headers)
        if not self.valid(token):
            raise denied()
        handler.app.workspaces.sessions.relay(handler, token)

    def guard_cancel(self, sessions, pid, sid):
        # Ordinary GUI cancellation has no authority over an owned terminal.
        with self.lock:
            pending = any(e['scope'] == (pid, sid) and not e['closed'] and e['opening']
                          for e in self.reservations.values())
        if pending or sessions.terminals.scope_active(pid, sid):
            raise denied()

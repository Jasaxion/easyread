"""Remote terminal reservations: no SSH host, native Agent or provider touched."""
from contextlib import contextmanager
from email.message import Message
import os
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from easyread.workspaces import WorkspaceError, remote_terminal, remote_terminal_worker, remote_environment

PID, SID = 'a' * 20, 'abcdef'
URL = '/workspace-terminal/' + '1' * 32 + '/'


class Access:
    def __init__(self):
        self.owners = {'alice', 'bob'}

    def valid(self, owner):
        return owner in self.owners


class Connection:
    def __init__(self):
        self.connected = True
        self.active = 0
        self.calls = []
        self.close_failed = False
        self.opened = False
        self.result = {'url': URL, 'scope': [PID, SID], 'native_id': 'same-native-id', 'engine': 'claude'}
        self.on_open = None

    def start(self):
        pass

    def json(self, method, path, body=None, *, headers=None, require_connected=False):
        self.calls.append((method, path, body, headers, require_connected))
        if path.endswith('/close'):
            if self.close_failed:
                raise WorkspaceError('lost cleanup response', 503)
            self.opened = False
            return {'ok': True}
        if path.endswith('/status'):
            return {'active': self.opened, 'scope': [PID, SID]}
        self.opened = True
        if self.on_open:
            self.on_open()
        return self.result


class RemoteTerminalReservationsTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'EASYREAD_TERMINAL': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.conn = Connection()
        self.leases = 0
        self.access = Access()
        self.manager = remote_terminal.RemoteTerminals(self, self.access)
        self.addCleanup(self.manager.close)
        # Deterministic explicit reaping/cleanup in this unit fixture.
        self.manager.stopped.set()
        self.manager.wake.set()
        self.manager.reaper.join(5)
        self.manager.stopped.clear()

    @contextmanager
    def connection(self, server):
        self.leases += 1
        try:
            yield self.conn
        finally:
            self.leases -= 1

    def open(self, owner='alice'):
        return self.manager.open('server', PID, SID, owner)

    def test_remote_needs_no_local_ttyd_and_reopen_reuses_native_identity_and_lease(self):
        with patch('shutil.which', return_value=None):
            first, second = self.open(), self.open()
        self.assertEqual(first, second)
        self.assertEqual(first['native_id'], 'same-native-id')
        self.assertEqual(self.leases, 1)
        self.assertEqual(sum(c[1].endswith('/terminal') for c in self.conn.calls), 1)
        token = self.conn.calls[0][3][remote_terminal_worker.OWNER_HEADER]
        self.assertEqual(len(token), 64)
        self.assertNotIn('alice', token)
        self.assertNotIn(token, str(first))
        self.assertTrue(all(c[4] for c in self.conn.calls))
        with self.assertRaises(WorkspaceError):
            self.open('bob')
        with self.assertRaises(WorkspaceError):
            self.manager.close_scope('server', PID, SID, 'bob')
        self.assertTrue(self.conn.opened)
        self.assertTrue(self.manager.close_scope('server', PID, SID, 'alice'))
        self.assertFalse(self.conn.opened)
        self.assertEqual(self.leases, 0)

    def test_invalid_worker_url_and_scope_never_published_and_cleaned_by_owner(self):
        for changes in ({'url': 'http://evil/terminal/'}, {'url': URL + '?token=secret'},
                        {'url': URL + '../api/'}, {'scope': [PID, 'other']}, {'url': '//evil/path'}):
            with self.subTest(changes=changes):
                self.conn.result = {'url': URL, 'scope': [PID, SID], **changes}
                with self.assertRaises(WorkspaceError) as error:
                    self.open()
                self.assertEqual(error.exception.code, 'remote_protocol')
                self.assertFalse(self.manager.handles)
                self.assertFalse(self.conn.opened)
                self.assertEqual(self.leases, 0)
                self.assertTrue(self.conn.calls[-1][1].endswith('/close'))

    def test_disabled_gate_revoked_user_and_dead_generation_cannot_open_or_attach(self):
        with patch.dict(os.environ, {'EASYREAD_TERMINAL': '0'}):
            with self.assertRaises(WorkspaceError):
                self.open()
        self.assertFalse(self.conn.calls)
        self.open()
        entry = next(iter(self.manager.entries.values()))
        self.conn.connected = False
        self.assertFalse(self.manager.valid(entry))
        with self.assertRaises(WorkspaceError):
            self.open()
        self.manager._cleanup(entry)
        self.assertEqual(self.leases, 0)
        self.assertEqual(len(self.conn.calls), 1)  # Never restart for status/close.
        self.manager.revoke('alice')
        with self.assertRaises(WorkspaceError):
            self.open()

    def test_lost_open_response_retains_reservation_until_scoped_cleanup_ack(self):
        def lost():
            raise WorkspaceError('lost response', 503)
        self.conn.on_open = lost
        self.conn.close_failed = True
        with self.assertRaises(WorkspaceError):
            self.open()
        self.assertTrue(self.conn.opened)
        self.assertEqual(self.leases, 1)
        self.assertFalse(self.manager.handles)
        with self.assertRaises(WorkspaceError):
            self.open('bob')
        self.assertEqual(sum(c[1].endswith('/terminal') for c in self.conn.calls), 1)
        entry = next(iter(self.manager.entries.values()))
        self.manager.wake.clear()
        self.manager._retire(entry)
        self.assertFalse(self.manager.wake.is_set())  # Failed cleanup must not busy-spin.
        self.conn.close_failed = False
        self.assertTrue(self.manager._cleanup(entry))
        self.assertEqual(self.leases, 0)
        self.assertFalse(self.conn.opened)

    def test_logout_during_late_open_never_publishes_and_retains_until_cleanup(self):
        started, finish = threading.Event(), threading.Event()
        def pause():
            started.set()
            self.assertTrue(finish.wait(5))
        self.conn.on_open = pause
        errors = []
        def run():
            try:
                self.open()
            except WorkspaceError as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(started.wait(5))
        self.manager.revoke('alice')
        self.assertEqual(self.leases, 1)
        self.assertFalse(self.manager.handles)
        finish.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].status, 403)
        self.assertFalse(self.conn.opened)
        self.assertEqual(self.leases, 0)


class RemoteTerminalWorkerOwnersTest(unittest.TestCase):
    def setUp(self):
        self.owners = remote_terminal_worker.TerminalOwners()
        self.closed = []
        self.sessions = SimpleNamespace(terminals=SimpleNamespace(close=self.closed.append))
        self.token = 'a' * 64

    def test_bearer_independent_scope_token_required_and_duplicate_rejected(self):
        for values in ([], ['alice'], [self.token, self.token]):
            headers = Message()
            for value in values:
                headers[remote_terminal_worker.OWNER_HEADER] = value
            with self.assertRaises(WorkspaceError):
                self.owners.token(headers)
        headers = Message()
        headers[remote_terminal_worker.OWNER_HEADER] = self.token
        self.assertEqual(self.owners.token(headers), self.token)

    def test_close_before_open_tombstones_token_and_never_cancels_other_writer(self):
        self.assertEqual(self.owners.close(self.sessions, PID, SID, self.token), {'ok': True})
        self.assertEqual(self.closed, [self.token])
        with self.assertRaises(WorkspaceError):
            self.owners.open(self.sessions, PID, SID, self.token)
        with self.assertRaises(WorkspaceError):
            self.owners.close(self.sessions, PID, 'different', self.token)
        self.assertEqual(self.closed, [self.token])

    def test_reservation_capacity_fails_closed_but_unknown_cleanup_is_acknowledged(self):
        with patch.object(remote_terminal_worker, 'MAX_RESERVATIONS', 1):
            self.owners.reserve(self.token, (PID, SID))
            other = 'b' * 64
            with self.assertRaises(WorkspaceError) as error:
                self.owners.reserve(other, (PID, SID))
            self.assertEqual(error.exception.status, 429)
            self.assertEqual(self.owners.close(self.sessions, PID, SID, other), {'ok': True})
            self.assertEqual(self.closed, [])
            with self.assertRaises(WorkspaceError):
                self.owners.reserve(other, (PID, SID))

    def test_remote_capabilities_probe_actual_ttyd_and_explicit_enable(self):
        for found, switch, expected in ((None, '1', False), ('/remote/ttyd', '0', False), ('/remote/ttyd', '1', True)):
            with patch('shutil.which', return_value=found), patch.dict(os.environ, {'EASYREAD_TERMINAL': switch}):
                result = remote_environment.capabilities()['terminal']
                self.assertEqual(result['available'], expected)
                self.assertEqual(bool(result['reason']), not expected)


if __name__ == '__main__':
    unittest.main()

"""Real deployment/worker HTTP and WS relays with a controlled ttyd peer.

OpenSSH SOCKS connection creation is replaced by loopback HTTP, not the relay.
No real native Agent, provider, SSH server or user's library is contacted.
"""
import base64
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import socket
import threading
import time
import unittest
from unittest.mock import patch

from easyread.workspaces import session_cli, session_terminal, remote_transport
from easyread.workspaces.remote_terminal_worker import OWNER_HEADER
from tests import test_workspace_remote_http as remote_http


def receive(sock, count):
    data = bytearray()
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise EOFError(bytes(data))
        data.extend(part)
    return bytes(data)


def handshake(sock):
    data = bytearray()
    while not data.endswith(b'\r\n\r\n'):
        data.extend(receive(sock, 1))
        if len(data) > 16384:
            raise AssertionError('oversized WS handshake')
    return bytes(data)


class PeerProcess:
    def __init__(self):
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None


class TerminalPeer(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.server.requests.append((self.path, dict(self.headers)))
        if self.headers.get('Upgrade', '').lower() == 'websocket':
            self.close_connection = True
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            key = self.headers['Sec-WebSocket-Key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
            self.send_header('Sec-WebSocket-Accept', base64.b64encode(hashlib.sha1(key.encode()).digest()).decode())
            self.send_header('Sec-WebSocket-Protocol', self.headers['Sec-WebSocket-Protocol'])
            self.end_headers()
            try:
                while True:
                    data = self.connection.recv(65536)
                    if not data:
                        return
                    self.server.input.extend(data)
                    self.connection.sendall(data)  # Test exact wire bytes, not a new WS engine.
            except OSError:
                return
        body = b'<html>embedded ttyd test peer</html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Worker-Secret', 'must-not-forward')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)


class RemoteTerminalHTTPTest(unittest.TestCase):
    setUp_remote = remote_http.WorkspaceRemoteHTTPTest.setUp
    tearDown_remote = remote_http.WorkspaceRemoteHTTPTest.tearDown
    setUp_local = remote_http.WorkspaceRemoteHTTPTest.setUp_local
    tearDown_local = remote_http.WorkspaceRemoteHTTPTest.tearDown_local
    request = remote_http.WorkspaceRemoteHTTPTest.request
    login = remote_http.WorkspaceRemoteHTTPTest.login
    allocated = remote_http.WorkspaceRemoteHTTPTest.allocated

    def setUp(self):
        self.setUp_remote()
        self.peer = ThreadingHTTPServer(('127.0.0.1', 0), TerminalPeer)
        self.peer.daemon_threads = True
        self.peer.requests, self.peer.input = [], bytearray()
        self.peer_thread = threading.Thread(target=self.peer.serve_forever, daemon=True)
        self.peer_thread.start()
        self.env_terminal = patch.dict(os.environ, {'EASYREAD_TERMINAL': '1'})
        self.env_terminal.start()
        self.available = patch.object(session_terminal, 'ttyd_available', return_value=True)
        self.available.start()
        self.argv = patch.object(session_cli, 'terminal_argv', return_value=['fake-native-agent'])
        self.argv.start()
        self.processes = []
        self.open_count = 0
        test = self

        def opened(terminals, *, access_sid, scope, **unused):
            with terminals.lock:
                test.open_count += 1
                proc = PeerProcess()
                test.processes.append(proc)
                key = secrets.token_hex(16)
                terminals.sessions[key] = {'sid': access_sid, 'scope': tuple(scope), 'proc': proc,
                    'port': test.peer.server_port, 'sockets': set(), 'created': time.monotonic()}
                return {'url': '/workspace-terminal/' + key + '/', 'scope': list(scope)}
        self.native_open = patch.object(session_terminal.NativeTerminals, 'open_native', opened)
        self.native_open.start()
        self.stop = patch.object(session_cli, 'stop_process', side_effect=lambda proc: setattr(proc, 'stopped', True))
        self.stop.start()
        self.pid, self.prefix = self.allocated()
        status, _, body = self.request(self.prefix + '/sessions/create', {'engine': 'claude'})
        self.assertEqual(status, 200, body)
        self.session = body['session']['id']
        self.native_id = body['session']['native_id']
        self.endpoint = self.prefix + '/sessions/' + self.session

    def tearDown(self):
        self.tearDown_remote()
        self.stop.stop()
        self.native_open.stop()
        self.argv.stop()
        self.available.stop()
        self.env_terminal.stop()
        self.peer.shutdown()
        self.peer.server_close()
        self.peer_thread.join(5)

    def open(self):
        status, _, body = self.request(self.endpoint + '/terminal', {})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['native_id'], self.native_id)
        return body

    def worker_request(self, path, *, owner=None, bearer=True, method='GET'):
        conn = http.client.HTTPConnection('127.0.0.1', self.worker.server_port, timeout=5)
        headers = {'Authorization': 'Bearer ' + self.worker.token} if bearer else {}
        if owner:
            headers[OWNER_HEADER] = owner
        try:
            conn.request(method, path, b'{}' if method == 'POST' else None, headers)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def ws(self, url, *, cookie=None, origin=None, extra=''):
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=5)
        sock.settimeout(5)
        self.addCleanup(sock.close)
        host = f'127.0.0.1:{self.port}'
        sock.sendall((f'GET {url}ws HTTP/1.1\r\nHost: {host}\r\n'
                      f'Cookie: {self.cookie if cookie is None else cookie}\r\nX-Token: {self.token}\r\n'
                      'Authorization: browser-must-not-reach-worker\r\n'
                      f'Origin: {origin or "http://" + host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                      'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n'
                      f'Sec-WebSocket-Protocol: tty\r\n{extra}\r\n').encode())
        reply = handshake(sock)
        if b'101' not in reply.split(b'\r\n')[0]:
            for line in reply.split(b'\r\n')[1:]:
                if line.lower().startswith(b'content-length:'):
                    receive(sock, int(line.split(b':', 1)[1]))
        return sock, reply

    def wait_closed(self):
        until = time.monotonic() + 5
        while time.monotonic() < until and self.manager.terminals.entries:
            time.sleep(.02)
        self.assertFalse(self.manager.terminals.entries)
        self.assertFalse(self.worker.app.workspaces.sessions.terminals.scope_active(self.pid, self.session))

    def test_http_scope_auth_headers_and_no_duplicate_spawn(self):
        first = self.open()
        self.assertEqual(first, self.open())
        self.assertEqual(self.open_count, 1)
        self.assertEqual(self.request(self.endpoint)[2]['session']['native_id'], self.native_id)
        entry = next(iter(self.manager.terminals.entries.values()))
        self.assertEqual(self.manager.leases[self.sid], 1)
        self.assertEqual(self.request('/api/workspaces/servers/' + self.sid + '/disconnect', {})[0], 409)
        for method in ('GET', 'HEAD'):
            status, headers, body = self.request(first['url'], method=method,
                headers={'Authorization': 'browser-secret', OWNER_HEADER: 'forged', 'X-Forwarded-Host': 'evil'})
            self.assertEqual(status, 200)
            self.assertEqual(headers['X-Frame-Options'], 'SAMEORIGIN')
            self.assertNotIn('X-Worker-Secret', headers)
            self.assertNotIn(entry['owner'], str(headers) + str(body))
            forwarded = self.peer.requests[-1][1]
            for name in ('Cookie', 'X-Token', 'Authorization', OWNER_HEADER, 'X-Forwarded-Host'):
                self.assertNotIn(name, forwarded)
        self.assertEqual(self.worker_request(first['url'], bearer=False)[0], 401)
        self.assertEqual(self.worker_request(first['url'])[0], 403)
        self.assertEqual(self.worker_request(first['url'], owner='f' * 64)[0], 403)
        self.assertEqual(self.request(first['url'], auth=False)[0], 401)
        for suffix in ('?arg=whoami', '../status', '%2e%2e/status', '//evil'):
            self.assertIn(self.request(first['url'] + suffix)[0], (400, 403))
        self.assertEqual(self.request(self.endpoint + '/terminal/close', {})[0], 404)
        self.assertEqual(self.request(self.endpoint + '/send', {'message': 'no concurrent writer'})[0], 409)
        self.assertEqual(self.request(self.endpoint + '/cancel', {})[0], 200)
        self.assertEqual(self.manager.leases[self.sid], 0)
        self.assertTrue(self.processes[0].stopped)

    def test_ws_exact_bytes_subprotocol_resize_origin_and_disconnect_no_respawn(self):
        first = self.open()
        for origin in ('http://evil.example', f'http://127.0.0.1:{self.port + 1}'):
            sock, reply = self.ws(first['url'], origin=origin)
            self.assertIn(b'403', reply.split(b'\r\n')[0])
            sock.close()
        sock, reply = self.ws(first['url'], extra='Origin: http://duplicate\r\n')
        self.assertIn(b'403', reply.split(b'\r\n')[0])
        sock.close()
        sock, reply = self.ws(first['url'])
        self.assertIn(b'101', reply.split(b'\r\n')[0])
        self.assertIn(b'Sec-WebSocket-Protocol: tty', reply)
        self.assertNotIn(self.worker.token.encode(), reply)
        self.assertEqual(self.open_count, 1)
        peer_headers = self.peer.requests[-1][1]
        for name in ('Cookie', 'X-Token', 'Authorization', OWNER_HEADER):
            self.assertNotIn(name, peer_headers)
        # Opaque masked binary/control/resize wire data, including every byte
        # value and output larger than one relay chunk, must survive unchanged.
        payload = b'\x82\x80MASK' + b'1{"columns":120,"rows":48}' + bytes(range(256)) * 400
        writer = threading.Thread(target=sock.sendall, args=(payload,))
        writer.start()
        self.assertEqual(receive(sock, len(payload)), payload)
        writer.join(5)
        other, rejected = self.ws(first['url'])
        self.assertIn(b'403', rejected.split(b'\r\n')[0])
        other.close()
        sock.sendall(b'original-still-alive')
        self.assertEqual(receive(sock, 20), b'original-still-alive')
        sock.close()
        self.wait_closed()
        _, rejected = self.ws(first['url'])
        self.assertIn(b'403', rejected.split(b'\r\n')[0])
        self.assertEqual(self.open_count, 1)
        self.assertEqual(self.request(self.endpoint)[2]['session']['native_id'], self.native_id)

    def test_cross_browser_scope_and_logout_stop_only_owned_terminal(self):
        first = self.open()
        cookie_alice = self.cookie
        self.login()
        self.assertNotEqual(self.cookie, cookie_alice)
        self.assertEqual(self.request(first['url'])[0], 403)
        self.assertEqual(self.request(self.endpoint + '/terminal', {})[0], 403)
        self.assertEqual(self.request(self.endpoint + '/cancel', {})[0], 403)
        status, _, body = self.request(self.prefix + '/sessions/create', {'engine': 'pi'})
        self.assertEqual(status, 200, body)
        other_endpoint = self.prefix + '/sessions/' + body['session']['id']
        status, _, other = self.request(other_endpoint + '/terminal', {})
        self.assertEqual(status, 200, other)
        bob, reply = self.ws(other['url'])
        self.assertIn(b'101', reply)
        owners = {entry['url']: entry['owner'] for entry in self.manager.terminals.entries.values()}
        self.assertEqual(self.worker_request(first['url'], owner=owners[other['url']])[0], 403)
        alice, reply = self.ws(first['url'], cookie=cookie_alice)
        self.assertIn(b'101', reply)
        self.assertEqual(self.request('/api/access/logout', {})[0], 200)
        self.assertEqual(bob.recv(1), b'')
        alice.sendall(b'alice unaffected')
        self.assertEqual(receive(alice, 16), b'alice unaffected')
        self.assertFalse(self.processes[0].stopped)
        self.assertTrue(self.processes[1].stopped)
        alice.close()
        self.wait_closed()

    def test_encoded_cancel_cannot_bypass_cross_browser_terminal_ownership(self):
        opened = self.open()
        sock, reply = self.ws(opened['url'])
        self.assertIn(b'101', reply)
        self.login()  # A different authenticated browser does not own this terminal.
        for route, expected in (('/cancel%252f', 404), ('/cancel%2f', 403), ('/cancel/', 403)):
            with self.subTest(route=route):
                self.assertEqual(self.request(self.endpoint + route, {})[0], expected)
                self.assertFalse(self.processes[0].stopped)
                sock.sendall(b'owner remains writable')
                self.assertEqual(receive(sock, 22), b'owner remains writable')
        # Worker authorization must use the same canonical parts as dispatch,
        # even when called directly rather than through deployment forwarding.
        for route in ('/cancel/', '/cancel%2f', '/cancel///'):
            self.assertEqual(self.worker_request(self.endpoint + route, method='POST')[0], 403)
        self.assertEqual(self.request(self.endpoint)[2]['session']['native_id'], self.native_id)
        sock.close()
        self.wait_closed()

    def test_session_delete_requires_owner_scoped_stop_and_preserves_history(self):
        sessions = self.worker.app.workspaces.sessions
        sessions._commit(self.pid, lambda data: data['sessions'][self.session]['messages'].append(
            {'role': 'user', 'content': 'Keep this session history'}))
        history = self.request(self.endpoint + '/history')[2]['messages']
        opened = self.open()
        alice = self.cookie
        sock, reply = self.ws(opened['url'])
        self.assertIn(b'101', reply)
        self.login()
        for route, expected in (('/delete', 403), ('/delete/', 403), ('/delete%2f', 403), ('/delete%252f', 404)):
            self.assertEqual(self.request(self.endpoint + route, {})[0], expected)
            self.assertFalse(self.processes[0].stopped)
            self.assertEqual(self.request(self.endpoint + '/history')[2]['messages'], history)
            sock.sendall(b'not deleted')
            self.assertEqual(receive(sock, 11), b'not deleted')
        self.cookie = alice
        self.assertEqual(self.request(self.endpoint + '/delete', {})[0], 403)
        self.assertEqual(self.request(self.endpoint + '/cancel', {})[0], 200)
        self.assertEqual(sock.recv(1), b'')
        self.assertEqual(self.request(self.endpoint + '/delete', {})[0], 200)
        self.assertEqual(self.request(self.endpoint)[0], 404)

    def test_session_delete_rejects_pending_terminal_open(self):
        entered, release = threading.Event(), threading.Event()
        sessions = self.worker.app.workspaces.sessions
        original = sessions.terminal
        outcome = []
        def opening(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args, **kwargs)
        def open_terminal():
            outcome.append(self.request(self.endpoint + '/terminal', {}))
        with patch.object(sessions, 'terminal', side_effect=opening):
            thread = threading.Thread(target=open_terminal)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.login()
                self.assertEqual(self.request(self.endpoint + '/delete', {})[0], 403)
                self.assertEqual(self.open_count, 0)
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome[0][0], 200, outcome)
        self.assertEqual(self.request(self.endpoint)[2]['session']['native_id'], self.native_id)

    def test_missing_project_open_failure_releases_cleanup_reservation_and_lease(self):
        self.worker.app.workspaces.store.delete(self.pid)
        for _ in range(2):
            self.assertEqual(self.request(self.endpoint + '/terminal', {})[0], 404)
            self.assertFalse(self.manager.terminals.entries)
            self.assertEqual(self.manager.leases[self.sid], 0)
        self.assertEqual(self.open_count, 0)
        self.assertTrue(all(entry['closed'] for entry in self.worker.app.access.reservations.values()))
        owner = next(iter(self.worker.app.access.reservations))
        close = self.endpoint + '/terminal/close'
        self.assertEqual(self.worker_request(close, owner=owner, bearer=False, method='POST')[0], 401)
        self.assertEqual(self.worker_request(close, method='POST')[0], 403)
        self.assertEqual(self.worker_request(close, owner='invalid', method='POST')[0], 403)
        wrong_scope = self.prefix + '/sessions/abcdef/terminal/close'
        self.assertEqual(self.worker_request(wrong_scope, owner=owner, method='POST')[0], 403)
        # No metadata/environment lookup is needed to close a scoped process.
        self.worker.workspace_valid = False
        with patch.object(self.worker.app.workspaces.store, 'get', side_effect=AssertionError('cleanup must not read project')):
            self.assertEqual(self.worker_request(close, owner=owner, method='POST')[0], 200)
        self.assertEqual(self.request('/api/workspaces/servers/' + self.sid + '/disconnect', {})[0], 200)

    def test_lost_close_response_then_project_deletion_still_releases_lease(self):
        self.open()
        entry = next(iter(self.manager.terminals.entries.values()))
        connection = entry['conn']
        original = connection.json
        def lose_close_response(method, path, *args, **kwargs):
            result = original(method, path, *args, **kwargs)
            if path.endswith('/terminal/close'):
                raise remote_transport.unavailable()
            return result
        with patch.object(connection, 'json', side_effect=lose_close_response):
            self.assertEqual(self.request(self.endpoint + '/cancel', {})[0], 409)
            self.assertTrue(self.processes[0].stopped)
            self.assertEqual(self.manager.leases[self.sid], 1)
            self.assertEqual(self.request(self.prefix + '/delete', {})[0], 200)
            self.assertIn(entry, self.manager.terminals.entries.values())
        self.assertTrue(self.manager.terminals._cleanup(entry, wait=True))
        self.assertFalse(self.manager.terminals.entries)
        self.assertEqual(self.manager.leases[self.sid], 0)
        self.assertEqual(self.request('/api/workspaces/servers/' + self.sid + '/disconnect', {})[0], 200)

    def test_live_disable_closes_socket_and_releases_native_owner(self):
        opened = self.open()
        sock, reply = self.ws(opened['url'])
        self.assertIn(b'101', reply)
        os.environ['EASYREAD_TERMINAL'] = '0'
        self.assertEqual(sock.recv(1), b'')
        self.wait_closed()
        self.assertTrue(self.processes[0].stopped)
        self.assertEqual(self.request(self.endpoint + '/terminal', {})[0], 409)
        self.assertEqual(self.open_count, 1)

    def test_local_native_terminal_still_uses_same_relay_without_remote_mapping(self):
        project = self.app.workspaces.store.allocate({'name': 'Local regression', 'engine': 'claude'})
        prefix = '/api/workspaces/' + project['id']
        status, _, result = self.request(prefix + '/sessions/create', {'engine': 'claude'})
        self.assertEqual(status, 200, result)
        endpoint = prefix + '/sessions/' + result['session']['id']
        status, _, opened = self.request(endpoint + '/terminal', {})
        self.assertEqual(status, 200, opened)
        self.assertFalse(self.manager.terminals.entries)
        self.assertEqual(self.request(opened['url'])[0], 200)
        sock, reply = self.ws(opened['url'])
        self.assertIn(b'101', reply)
        sock.sendall(b'local resize/control\x00\x03')
        self.assertEqual(receive(sock, 22), b'local resize/control\x00\x03')
        self.assertEqual(self.request(endpoint + '/cancel', {})[0], 200)
        self.assertEqual(sock.recv(1), b'')
        self.assertTrue(self.processes[0].stopped)
        self.assertFalse(self.app.workspaces.sessions.terminals.scope_active(project['id']))

    def test_gui_cancel_ownership_check_serializes_with_terminal_admission(self):
        entered, release = threading.Event(), threading.Event()
        original = self.worker.app.access.guard_cancel
        def guard(*args):
            original(*args)
            entered.set()
            self.assertTrue(release.wait(5))
        outcome = []
        def cancel():
            outcome.append(self.request(self.endpoint + '/cancel', {}))
        with patch.object(self.worker.app.access, 'guard_cancel', side_effect=guard):
            thread = threading.Thread(target=cancel)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                status, _, body = self.request(self.endpoint + '/terminal', {})
                self.assertEqual(status, 409, body)
                self.assertEqual(self.open_count, 0)
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome[0][0], 200, outcome)
        self.assertFalse(self.manager.terminals.entries)

    def test_worker_close_is_owner_scoped_and_stale_token_cannot_kill_replacement(self):
        self.open()
        entry = next(iter(self.manager.terminals.entries.values()))
        owner = entry['owner']
        endpoint = self.endpoint + '/terminal'
        self.assertEqual(self.worker_request(endpoint + '/close', owner=owner, method='POST')[0], 200)
        self.assertEqual(self.request(self.endpoint + '/cancel', {})[0], 200)
        replacement = self.open()
        self.assertEqual(self.worker_request(endpoint + '/close', owner=owner, method='POST')[0], 200)
        self.assertEqual(self.request(replacement['url'])[0], 200)
        self.assertFalse(self.processes[-1].stopped)
        self.assertEqual(self.worker_request(endpoint, owner=owner, method='POST')[0], 403)


if __name__ == '__main__':
    unittest.main()

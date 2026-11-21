"""Optional real ttyd/PTTY round trip through both existing relay layers.

Run with ttyd on PATH (an extracted package is sufficient). Uses a tiny local
Python PTY program, no native Agent, SSH server, credentials or real library.
"""
import json
import os
import shutil
import signal
import struct
import sys
import time
import unittest
from unittest.mock import patch

from easyread.workspaces import session_cli
from tests import test_workspace_remote_http as remote_http
from tests import test_workspace_remote_terminal_http as relay_http


PROGRAM = r'''
import fcntl, json, os, signal, struct, sys, termios, tty
state = sys.argv[1]
tty.setraw(0)
def resized(*args):
    rows, columns, _, _ = struct.unpack('HHHH', fcntl.ioctl(0, termios.TIOCGWINSZ, b'\0' * 8))
    with open(state, 'w') as stream:
        json.dump({'pid': os.getpid(), 'columns': columns, 'rows': rows}, stream)
signal.signal(signal.SIGWINCH, resized)
resized()
os.write(1, b'REMOTE-PTY-READY')
while True:
    data = os.read(0, 4096)
    if not data:
        break
    os.write(1, data)
'''


def send_frame(sock, payload):
    mask = b'test'
    size = len(payload)
    header = bytes((0x82, 0x80 | size)) if size < 126 else bytes((0x82, 0x80 | 126)) + struct.pack('!H', size)
    sock.sendall(header + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))


def frame(sock):
    head = relay_http.receive(sock, 2)
    size = head[1] & 127
    if size == 126:
        size = struct.unpack('!H', relay_http.receive(sock, 2))[0]
    elif size == 127:
        size = struct.unpack('!Q', relay_http.receive(sock, 8))[0]
    if size > 1024 * 1024:
        raise AssertionError('oversized test frame')
    return relay_http.receive(sock, size)


@unittest.skipUnless(os.name == 'posix' and shutil.which('ttyd'), 'optional real ttyd not on PATH')
class RemoteTerminalTtydTest(unittest.TestCase):
    setUp_local = remote_http.WorkspaceRemoteHTTPTest.setUp_local
    tearDown_local = remote_http.WorkspaceRemoteHTTPTest.tearDown_local
    request = remote_http.WorkspaceRemoteHTTPTest.request
    login = remote_http.WorkspaceRemoteHTTPTest.login
    allocated = remote_http.WorkspaceRemoteHTTPTest.allocated
    ws = relay_http.RemoteTerminalHTTPTest.ws
    wait_closed = relay_http.RemoteTerminalHTTPTest.wait_closed

    def setUp(self):
        remote_http.WorkspaceRemoteHTTPTest.setUp(self)
        self.enabled = patch.dict(os.environ, {'EASYREAD_TERMINAL': '1'})
        self.enabled.start()
        self.program = self.remote_root / 'pty-program.py'
        self.program.write_text(PROGRAM)
        self.state = self.remote_root / 'pty-state.json'
        self.argv = patch.object(session_cli, 'terminal_argv', return_value=[sys.executable, str(self.program), str(self.state)])
        self.argv.start()
        self.pid, prefix = self.allocated()
        status, _, body = self.request(prefix + '/sessions/create', {'engine': 'claude'})
        self.assertEqual(status, 200, body)
        self.session = body['session']['id']
        self.native_id = body['session']['native_id']
        self.endpoint = prefix + '/sessions/' + self.session

    def tearDown(self):
        remote_http.WorkspaceRemoteHTTPTest.tearDown(self)
        self.argv.stop()
        self.enabled.stop()

    def await_state(self, columns=None):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                state = json.loads(self.state.read_text())
                if columns is None or state['columns'] == columns:
                    return state
            except (FileNotFoundError, ValueError):
                pass
            time.sleep(.02)
        self.fail('PTY did not report the expected resize')

    def test_real_ttyd_handshake_input_resize_close_and_native_identity(self):
        status, _, opened = self.request(self.endpoint + '/terminal', {})
        self.assertEqual(status, 200, opened)
        self.assertEqual(opened['native_id'], self.native_id)
        self.assertEqual(self.request(opened['url'])[0], 200)
        sock, headers = self.ws(opened['url'])
        self.assertIn(b'101', headers.split(b'\r\n')[0])
        self.assertIn(b'sec-websocket-protocol: tty', headers.lower())
        send_frame(sock, json.dumps({'AuthToken': '', 'columns': 80, 'rows': 24}).encode())
        output = b''
        for _ in range(20):
            output += frame(sock)
            if b'REMOTE-PTY-READY' in output:
                break
        self.assertIn(b'REMOTE-PTY-READY', output)
        state = self.await_state()
        process_id = state['pid']
        send_frame(sock, b'1' + json.dumps({'columns': 123, 'rows': 47}).encode())
        state = self.await_state(123)
        self.assertEqual(state['rows'], 47)
        payload = b'input\x1b[A\x03\x00\x7f-end'
        send_frame(sock, b'0' + payload)
        output = b''
        for _ in range(20):
            output += frame(sock)
            if payload in output:
                break
        self.assertIn(payload, output)
        # Hiding a view calls no close; explicit repeated open reuses ttyd/CLI.
        self.assertEqual(self.request(self.endpoint + '/terminal', {})[2]['url'], opened['url'])
        self.assertEqual(self.await_state()['pid'], process_id)
        sock.close()  # Refresh/disconnect is intentionally not VTE persistence.
        self.wait_closed()
        self.assertEqual(self.request(self.endpoint)[2]['session']['native_id'], self.native_id)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(process_id, 0)
            except ProcessLookupError:
                break
            time.sleep(.02)
        else:
            self.fail('ttyd disconnect left its native PTY process running')


if __name__ == '__main__':
    unittest.main()

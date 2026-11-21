"""SSH policy, framing, startup bounds and private worker surface regressions."""
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from easyread.workspaces import WorkspaceError
from easyread.workspaces import remote_environment, remote_transport, remote_worker
from easyread.workspaces.remote_config import Registry, validate


def config(root='/tmp/research'):
    return validate({'name': 'Server', 'host': 'host.example', 'user': 'research', 'workspace': root})


class WorkspaceRemoteTransportTest(unittest.TestCase):
    def test_config_rejects_injection_options_credentials_and_invalid_paths(self):
        base = config()
        for field, bad in (('host', '-oProxyCommand=touch /tmp/pwn'), ('host', 'host;false'),
                           ('user', 'root@other'), ('user', '-root'), ('user', ''),
                           ('port', '22'), ('port', True), ('port', 65536),
                           ('workspace', 'relative'), ('workspace', '/'), ('workspace', '/a/../b'),
                           ('workspace', '/a//b'), ('workspace', '/a\nexec x'),
                           ('python', 'python3 -c x'), ('python', '/bin/python;false'),
                           ('identity_file', 'relative-key'), ('password', 'secret'),
                           ('private_key', 'secret'), ('ssh_args', ['-oStrictHostKeyChecking=no'])):
            with self.subTest(field=field, value=bad), self.assertRaises(WorkspaceError):
                validate({**base, field: bad})
        self.assertEqual(validate({**base, 'host': '::1'})['host'], '::1')
        self.assertEqual(validate({**base, 'workspace': "/tmp/a ' quoted; safe"})['workspace'], "/tmp/a ' quoted; safe")

    def test_ssh_flags_strict_auth_no_agent_forwarding_no_shell_and_quoted_workspace(self):
        body = config("/tmp/research ' quoted; $(bad)")
        with patch.object(remote_transport.shutil, 'which', return_value='/usr/bin/ssh'):
            args = remote_transport.ssh_command(body, 12345)
        for flag in ('BatchMode=yes', 'StrictHostKeyChecking=yes', 'ForwardAgent=no', 'ForwardX11=no',
                     'PermitLocalCommand=no', 'ExitOnForwardFailure=yes'):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index('-F') + 1], os.devnull)
        self.assertEqual(args[args.index('-D') + 1], '127.0.0.1:12345')
        self.assertEqual(args[-2], body['host'])
        import shlex
        self.assertEqual(shlex.split(args[-1]), ['python3', '-m', 'easyread.workspaces.remote_worker', '--workspace', body['workspace']])
        self.assertNotIn('SendEnv', ' '.join(args))

    def test_startup_token_only_stdin_private_env_and_bounded_banner(self):
        connection = remote_transport.Connection(config())
        proc = MagicMock()
        proc.stdin = io.BytesIO()
        proc.stdout = io.BytesIO(b'login banner\n' + b'EASYREAD_REMOTE ' + json.dumps({
            'port': 12345, 'protocol': remote_transport.PROTOCOL, 'build': remote_transport.build_id()}).encode() + b'\n')
        proc.stderr = io.BytesIO(b'provider secret should never be surfaced')
        proc.poll.return_value = None
        with patch.object(remote_transport.subprocess, 'Popen', return_value=proc) as popen, patch.object(remote_transport.shutil, 'which', return_value='/usr/bin/ssh'), patch.dict(os.environ, {'OPENAI_API_KEY': 'deployment-secret', 'ANTHROPIC_API_KEY': 'never-forward'}):
            connection.start()
        bootstrap = json.loads(proc.stdin.getvalue())
        self.assertEqual(bootstrap['token'], connection.token)
        args, kwargs = popen.call_args
        self.assertNotIn(connection.token, repr(args))
        self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
        self.assertNotIn('ANTHROPIC_API_KEY', kwargs['env'])
        self.assertFalse(kwargs.get('shell', False))
        connection.close()

    def test_worker_build_mismatch_and_oversized_startup_are_bounded(self):
        for output in (b'x' * 200000, b'EASYREAD_REMOTE {"port":123,"protocol":1,"build":"wrong"}\n'):
            connection = remote_transport.Connection(config())
            proc = MagicMock()
            proc.stdin, proc.stdout, proc.stderr = io.BytesIO(), io.BytesIO(output), io.BytesIO()
            proc.poll.return_value = None
            with patch.object(remote_transport.subprocess, 'Popen', return_value=proc), patch.object(remote_transport.shutil, 'which', return_value='/usr/bin/ssh'):
                with self.assertRaises(WorkspaceError) as failure:
                    connection.start()
            self.assertEqual(failure.exception.code, 'remote_unavailable')
            self.assertNotIn('wrong', str(failure.exception))
            self.assertTrue(connection._closed)

    def test_mutation_loss_is_not_replayed_or_used_to_kill_unknown_task(self):
        connection = remote_transport.Connection(config())
        connection.proc = MagicMock()
        connection.proc.poll.return_value = None
        connection.remote_port = 123
        transport = MagicMock()
        transport.getresponse.side_effect = http.client.RemoteDisconnected('response lost')
        with patch.object(remote_transport, 'TunnelHTTP', return_value=transport):
            with self.assertRaises(WorkspaceError) as failure:
                connection.json('POST', '/api/workspaces/create', {'name': 'once'})
        self.assertEqual(failure.exception.code, 'remote_unavailable')
        self.assertEqual(transport.request.call_count, 1)
        self.assertEqual(connection.active, 0)
        self.assertFalse(connection._closed, 'unknown remote work must not be assumed idle')
        self.assertEqual(connection.pool, [])
        connection.close()

    def test_frozen_build_uses_packaged_source_fingerprint(self):
        expected = remote_transport.build_id()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'remote-build.txt').write_text(expected + '\n')
            remote_transport.build_id.cache_clear()
            try:
                with patch.object(sys, 'frozen', True, create=True), patch.object(remote_transport, '__file__', str(Path(tmp) / 'remote_transport.py')):
                    self.assertEqual(remote_transport.build_id(), expected)
                    remote_transport.build_id.cache_clear()
                    (Path(tmp) / 'remote-build.txt').unlink()
                    with self.assertRaises(WorkspaceError) as failure:
                        remote_transport.build_id()
                    self.assertEqual(failure.exception.code, 'remote_protocol')
            finally:
                remote_transport.build_id.cache_clear()

    def test_socks_handshake_targets_only_remote_loopback(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        received = []

        def peer():
            sock, _ = listener.accept()
            with sock:
                received.append(remote_transport._receive(sock, 3))
                sock.sendall(b'\x05\x00')
                received.append(remote_transport._receive(sock, 10))
                sock.sendall(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
            listener.close()

        thread = threading.Thread(target=peer)
        thread.start()
        connection = remote_transport.TunnelHTTP(listener.getsockname()[1], 12345)
        try:
            connection.connect()
        finally:
            connection.close()
            thread.join(5)
        self.assertEqual(received[0], b'\x05\x01\x00')
        self.assertEqual(received[1], b'\x05\x01\x00\x01\x7f\x00\x00\x01\x30\x39')


class WorkspaceRemoteEnvironmentTest(unittest.TestCase):
    def test_missing_required_tools_and_optional_terminal_are_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(remote_environment.shutil, 'which', return_value=None), patch.object(remote_environment.session_cli, 'executable', return_value=None), patch.object(remote_environment.importlib.util, 'find_spec', return_value=object()):
            result = remote_environment.check(tmp, 'pi')
        self.assertFalse(result['ok'])
        checks = {item['name']: item for item in result['checks']}
        for name in ('git', 'bd', 'dolt', 'latexmk', 'pdflatex', 'agent'):
            self.assertFalse(checks[name]['ok'])
            self.assertTrue(checks[name]['required'])
        self.assertFalse(checks['ttyd']['required'])
        self.assertFalse(result['capabilities']['terminal']['available'])

    def test_versions_are_runnable_and_beads_supported_range_is_fresh(self):
        def run(args, cwd, env):
            if args[0].endswith('/bd'):
                return 'bd version 1.3.4\n'
            return 'tool version 1.0\n'
        with tempfile.TemporaryDirectory() as tmp, patch.object(remote_environment.shutil, 'which', side_effect=lambda name: '/test/' + name), patch.object(remote_environment.session_cli, 'executable', return_value='/test/codex'), patch.object(remote_environment.beads_cli, 'execute', side_effect=run) as execute, patch.object(remote_environment.importlib.util, 'find_spec', return_value=object()):
            with patch.dict(os.environ, {'EASYREAD_BEADS_COMMAND': 'bd', 'EASYREAD_GIT_COMMAND': 'git'}):
                ready = remote_environment.check(tmp)
                self.assertTrue(ready['ok'], ready)
                self.assertTrue(any(call.args[0] == ['/test/bd', '--version'] for call in execute.call_args_list))
                execute.side_effect = lambda args, cwd, env: 'bd version 9.0.0' if args[0] == '/test/bd' else 'version 1.0'
                unsupported = remote_environment.check(tmp)
                self.assertFalse(unsupported['ok'])
                self.assertFalse(next(c for c in unsupported['checks'] if c['name'] == 'bd')['ok'])

    def test_invalid_workspace_and_engine_never_allocate(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(remote_environment.shutil, 'which', return_value=None):
            missing = Path(tmp) / 'not-created'
            result = remote_environment.check(str(missing))
            self.assertFalse(next(c for c in result['checks'] if c['name'] == 'workspace')['ok'])
            self.assertFalse(missing.exists())
            with self.assertRaises(WorkspaceError):
                remote_environment.check(tmp, 'arbitrary-command')


class WorkspaceRemoteWorkerTest(unittest.TestCase):
    def test_worker_requires_token_allowlists_surface_and_exits_on_stdin_eof(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = 'test-worker-private-token-with-enough-entropy'
            env = {**os.environ, 'PATH': '', 'EASYREAD_TERMINAL': '1'}
            proc = subprocess.Popen([sys.executable, '-m', 'easyread.workspaces.remote_worker', '--workspace', tmp],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                proc.stdin.write((json.dumps({'token': token, 'protocol': remote_transport.PROTOCOL, 'build': remote_transport.build_id()}) + '\n').encode())
                proc.stdin.flush()
                line = proc.stdout.readline(4096)
                self.assertTrue(line.startswith(b'EASYREAD_REMOTE '), line)
                ready = json.loads(line[len(b'EASYREAD_REMOTE '):])
                for path, auth, status in (('/status', None, 401), ('/check', None, 401),
                                           ('/api/workspaces/create', None, 401), ('/api/config', token, 404),
                                           ('/api/workspaces/servers', token, 404), ('/status', token, 200)):
                    connection = http.client.HTTPConnection('127.0.0.1', ready['port'], timeout=5)
                    try:
                        connection.request('GET', path, headers={'Authorization': 'Bearer ' + auth} if auth else {})
                        response = connection.getresponse()
                        self.assertEqual(response.status, status, (path, response.read()))
                        response.read()
                    finally:
                        connection.close()
                proc.stdin.close()
                proc.wait(timeout=15)
                self.assertEqual(proc.returncode, 0, proc.stderr.read().decode())
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    stream.close()
            self.assertFalse((Path(tmp) / 'workspace').exists())


if __name__ == '__main__':
    unittest.main()

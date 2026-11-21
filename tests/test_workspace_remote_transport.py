"""SSH policy, framing, startup bounds and private worker surface regressions."""
import http.client
import io
import json
import os
from pathlib import Path
import socket
import shlex
import shutil
import signal
import time
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
                           ('user', 'root@other'), ('user', '-root'), ('user', None),
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
                     'PermitLocalCommand=no', 'ExitOnForwardFailure=yes', 'ControlMaster=no', 'ControlPath=none',
                     'ControlPersist=no', 'RemoteCommand=none', 'ForkAfterAuthentication=no', 'StdinNull=no',
                     'RequestTTY=no', 'SessionType=default', 'UpdateHostKeys=no'):
            self.assertIn(flag, args)
        self.assertNotIn('-F', args)
        ignored = [option for option in args if option.startswith('IgnoreUnknown=')]
        self.assertEqual(ignored, ['IgnoreUnknown=SessionType,StdinNull,ForkAfterAuthentication'])
        for option in ('SessionType=default', 'StdinNull=no', 'ForkAfterAuthentication=no'):
            self.assertLess(args.index(ignored[0]), args.index(option))
        self.assertEqual(args[args.index('-D') + 1], '127.0.0.1:12345')
        self.assertEqual(args[-2], body['host'])
        self.assertEqual(shlex.split(args[-1].split('; exec ', 1)[1]),
                         ['python3', '-c', remote_transport.BOOTSTRAP, str(remote_transport.PROTOCOL),
                          remote_transport.build_id(), '--workspace', body['workspace']])
        self.assertNotIn('-p', args, 'omitted port must inherit config')
        self.assertNotIn('SendEnv', ' '.join(args))

    def test_optional_fields_inherit_and_existing_explicit_fields_remain_explicit(self):
        base = {'name': 'Alias', 'host': 'research_alias', 'workspace': '/tmp/research'}
        for port in (None, ''):
            normalized = validate({**base, 'port': port, 'user': '', 'identity_file': ''})
            self.assertEqual(normalized['user'], '')
            self.assertIsNone(normalized['port'])
            self.assertEqual(normalized['identity_file'], '')
        self.assertIsNone(validate(base)['port'])
        explicit = validate({**base, 'port': 22, 'user': 'research'})
        self.assertEqual((explicit['port'], explicit['user']), (22, 'research'))

    @unittest.skipUnless(shutil.which('ssh'), 'OpenSSH required')
    def test_openssh_resolves_disposable_alias_config_and_explicit_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity = root / 'identity'; identity.touch()
            selected = root / 'selected'; selected.touch()
            ssh_config = root / 'config'
            ssh_config.write_text(f'''Host research_alias
    HostName 127.0.0.1
    User inherited-user
    Port 2222
    IdentityFile {identity}
    ProxyJump disposable-jump
    ControlMaster auto
    ControlPersist 600
    ControlPath {root}/master
    RemoteCommand echo must-not-run
    RequestTTY force
    SessionType none
    ForkAfterAuthentication yes
    StdinNull yes
    StrictHostKeyChecking no
    BatchMode no
    ForwardAgent yes
    ForwardX11 yes
    PermitLocalCommand yes
''')
            def effective(body):
                args = remote_transport.ssh_command(validate(body), 12345)
                self.assertNotIn('-F', args)
                result = subprocess.run([args[0], '-F', str(ssh_config), '-G', *args[1:-1]],
                                        capture_output=True, text=True, timeout=5, check=True)
                return result.stdout
            body = {'name': 'Alias', 'host': 'research_alias', 'workspace': '/tmp/research'}
            inherited = effective(body)
            for setting in ('hostname 127.0.0.1', 'user inherited-user', 'port 2222',
                            f'identityfile {identity}', 'proxyjump disposable-jump',
                            'controlmaster false', 'controlpersist no', 'stricthostkeychecking true',
                            'batchmode yes', 'forwardagent no', 'forwardx11 no', 'permitlocalcommand no',
                            'requesttty false', 'sessiontype default', 'forkafterauthentication no', 'stdinnull no'):
                self.assertIn(setting + '\n', inherited)
            self.assertNotIn('must-not-run', inherited)
            self.assertNotIn(str(root / 'master'), inherited)
            explicit = effective({**body, 'user': 'override-user', 'port': 2200, 'identity_file': str(selected)})
            self.assertIn('user override-user\n', explicit)
            self.assertIn('port 2200\n', explicit)
            self.assertIn('identitiesonly yes\n', explicit)
            self.assertLess(explicit.index(f'identityfile {selected}'), explicit.index(f'identityfile {identity}'))

    def test_startup_token_only_stdin_private_env_and_bounded_banner(self):
        connection = remote_transport.Connection(config())
        proc = MagicMock(pid=2**30, returncode=None)
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
        class CountedStream(io.BytesIO):
            consumed = 0

            def readline(self, size=-1):
                line = super().readline(size)
                self.consumed += len(line)
                return line

        for output in (b'x' * 200000, b'EASYREAD_REMOTE {"port":123,"protocol":1,"build":"wrong"}\n'):
            connection = remote_transport.Connection(config())
            proc = MagicMock(pid=2**30, returncode=None)
            proc.stdin, proc.stdout, proc.stderr = io.BytesIO(), CountedStream(output), io.BytesIO()
            proc.poll.return_value = None
            with patch.object(remote_transport.subprocess, 'Popen', return_value=proc), patch.object(remote_transport.shutil, 'which', return_value='/usr/bin/ssh'):
                with self.assertRaises(WorkspaceError) as failure:
                    connection.start()
            self.assertEqual(failure.exception.code, 'remote_protocol' if output.startswith(b'EASYREAD_REMOTE ') else 'remote_worker_failed')
            self.assertNotIn('wrong', str(failure.exception))
            self.assertTrue(connection._closed)
            self.assertLessEqual(proc.stdout.consumed, 65536)

    def test_typed_startup_errors_never_echo_stderr_or_remote_marker_secrets(self):
        cases = [
            (b'ssh: Could not resolve hostname secret-host: Name or service not known', 'remote_dns'),
            (b'ssh: connect to host secret port 123: Connection refused', 'remote_network'),
            (b'Host key verification failed.', 'remote_host_key'),
            (b'secret-user: Permission denied (publickey).', 'remote_auth'),
            (b'ProxyCommand secret-token failed; unknown diagnostic', 'remote_worker_failed'),
        ]
        for stderr, expected in cases:
            with self.subTest(expected=expected):
                connection = remote_transport.Connection(config())
                proc = MagicMock(pid=2**30, returncode=255)
                proc.stdin, proc.stdout = io.BytesIO(), io.BytesIO()
                proc.stderr = io.BytesIO(b'PRIVATE KEY secret-token\n' * 10000 + stderr)
                with patch.object(remote_transport.subprocess, 'Popen', return_value=proc), patch.object(remote_transport, 'ssh_failure_code', wraps=remote_transport.ssh_failure_code) as classify:
                    with self.assertRaises(WorkspaceError) as error:
                        connection.start()
                self.assertLessEqual(len(classify.call_args.args[0]), remote_transport.MAX_START_STDERR)
                self.assertEqual(error.exception.code, expected)
                self.assertLess(len(str(error.exception)), 1000)
                for secret in ('secret-token', 'PRIVATE KEY', 'secret-host', connection.token):
                    self.assertNotIn(secret, str(error.exception))
                self.assertTrue(connection._closed)
                self.assertTrue(proc.stderr.closed)
        for code in ('remote_python', 'remote_worker_missing', 'remote_dependencies', 'remote_protocol', 'remote_busy'):
            connection = remote_transport.Connection(config())
            proc = MagicMock(pid=2**30, returncode=None)
            proc.stdin, proc.stderr = io.BytesIO(), io.BytesIO()
            proc.stdout = io.BytesIO(b'EASYREAD_REMOTE ' + json.dumps({'code': code, 'error': 'secret-token'}).encode() + b'\n')
            with patch.object(remote_transport.subprocess, 'Popen', return_value=proc):
                with self.assertRaises(WorkspaceError) as error:
                    connection.start()
            self.assertEqual(error.exception.code, code)
            self.assertEqual(error.exception.status, 409 if code == 'remote_busy' else 503)
            self.assertNotIn('secret-token', str(error.exception))

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group cleanup')
    def test_startup_deadline_reaps_only_owned_local_ssh_and_proxy_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / 'ssh'
            child_pid = Path(tmp) / 'child-pid'
            fake.write_text(f'#!{sys.executable}\nimport os, time, signal\nfrom pathlib import Path\n'
                            'if os.fork() == 0:\n'
                            '    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                            f'    Path({str(child_pid)!r}).write_text(str(os.getpid()))\n'
                            '    time.sleep(60)\n'
                            'time.sleep(60)\n')
            fake.chmod(0o700)
            connection = remote_transport.Connection(config())
            started = time.monotonic()
            with patch.object(remote_transport.shutil, 'which', return_value=str(fake)), patch.object(remote_transport, 'START_TIMEOUT', .1):
                with self.assertRaises(WorkspaceError) as error:
                    connection.start()
            self.assertEqual(error.exception.code, 'remote_start_timeout')
            self.assertLess(time.monotonic() - started, 10)
            self.assertIsNotNone(connection.proc.poll())
            self.assertTrue(connection.proc.stdout.closed)
            self.assertTrue(connection.proc.stderr.closed)
            self.assertFalse(connection.connected)
            if sys.platform == 'linux':
                stat = Path('/proc') / child_pid.read_text() / 'stat'
                deadline = time.monotonic() + 2
                while stat.exists() and stat.read_text().split()[2] != 'Z' and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(not stat.exists() or stat.read_text().split()[2] == 'Z', 'owned proxy child survived cleanup')
            with self.assertRaises(WorkspaceError):
                connection.start()  # A failed generation cannot restart itself.

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group cleanup')
    def test_already_reaped_leader_is_not_signalled_even_on_first_close(self):
        for code in (0, -9):
            with self.subTest(code=code):
                connection = remote_transport.Connection(config())
                proc = connection.proc = MagicMock(pid=2**30, returncode=code)
                proc.stdin, proc.stdout, proc.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
                with patch.object(remote_transport.os, 'killpg') as killpg:
                    connection.close()
                    killpg.assert_not_called()
                    waits = proc.wait.call_count
                    connection.close()
                    killpg.assert_not_called()
                    self.assertEqual(proc.wait.call_count, waits)
                self.assertEqual(connection.proc.returncode, code)
                self.assertTrue(proc.stdout.closed)
                self.assertTrue(proc.stderr.closed)

    @unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'WNOWAIT'), 'Linux unreaped ownership anchor')
    def test_normal_exit_observation_keeps_anchor_until_proxy_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            child_pid = Path(tmp) / 'proxy.pid'
            program = ('import os,time,signal; from pathlib import Path\n'
                       'if os.fork() == 0:\n'
                       ' signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                       f' Path({str(child_pid)!r}).write_text(str(os.getpid()))\n'
                       ' time.sleep(60)\n'
                       'else:\n os._exit(0)\n')
            connection = remote_transport.Connection(config())
            proc = connection.proc = subprocess.Popen([sys.executable, '-c', program], start_new_session=True,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            connection.remote_port = 123
            real_killpg = os.killpg
            observations = []
            try:
                deadline = time.monotonic() + 5
                while (connection.connected or not child_pid.exists()) and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(connection.connected)
                self.assertIsNone(proc.returncode, 'status polling must not reap the ownership anchor')
                self.assertEqual(remote_transport.process_exit(proc), (0, True))
                def kill_owned(pid, sig):
                    observations.append((proc.returncode, remote_transport.process_exit(proc)))
                    self.assertEqual(pid, proc.pid)
                    self.assertIsNone(proc.returncode, 'process-group signal must precede final wait/reap')
                    real_killpg(pid, sig)
                with patch.object(remote_transport.os, 'killpg', side_effect=kill_owned) as killpg:
                    connection.close()
                    count = killpg.call_count
                    self.assertGreater(count, 0)
                    connection.close()
                    self.assertEqual(killpg.call_count, count)
                self.assertEqual(proc.returncode, 0)
                self.assertTrue(all(owned for _, (_, owned) in observations))
                proxy_stat = Path('/proc') / child_pid.read_text() / 'stat'
                deadline = time.monotonic() + 2
                while proxy_stat.exists() and proxy_stat.read_text().split()[2] != 'Z' and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(not proxy_stat.exists() or proxy_stat.read_text().split()[2] == 'Z')
            finally:
                connection.close()

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group cleanup')
    def test_retirement_after_failed_startup_does_not_signal_cleaned_process_group(self):
        from easyread.workspaces.remote import Remotes

        with tempfile.TemporaryDirectory() as tmp, patch.object(remote_transport.os, 'killpg') as killpg:
            manager = Remotes(tmp)
            try:
                sid = manager.save_server(config())['server']['id']
                proc = MagicMock(pid=2**30, returncode=127)
                proc.stdin, proc.stdout, proc.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
                with patch.object(remote_transport.subprocess, 'Popen', return_value=proc) as popen, patch.object(remote_transport.shutil, 'which', return_value='/usr/bin/ssh'):
                    with manager.connection(sid) as failed:
                        with self.assertRaises(WorkspaceError) as error:
                            failed.start()
                    self.assertEqual(error.exception.code, 'remote_python', 'classification must retain the exit status after cleanup')
                    self.assertTrue(failed._closed)
                    signals = list(killpg.call_args_list)
                    self.assertEqual(len(signals), 0, 'an already-reaped startup failure has no owned PGID')
                    with manager.connection(sid) as replacement:
                        self.assertIsNot(replacement, failed)
                        self.assertIsNone(replacement.proc, 'retirement must not implicitly start another worker')
                    self.assertEqual(killpg.call_args_list, signals, 'retirement must not signal the old PGID again')
                    popen.assert_called_once()
                manager.close()
                self.assertEqual(killpg.call_args_list, signals)
            finally:
                manager.close()

    def test_missing_local_ssh_and_malformed_markers_are_typed(self):
        with patch.object(remote_transport.shutil, 'which', return_value=None):
            with self.assertRaises(WorkspaceError) as error:
                remote_transport.Connection(config()).start()
        self.assertEqual(error.exception.code, 'remote_ssh_client')
        for line in (b'{', b'[]', b'{"code":[]}', b'{"port":true}', b'{"code":"secret"}'):
            connection = remote_transport.Connection(config())
            proc = MagicMock(pid=2**30, returncode=None)
            proc.stdin, proc.stderr = io.BytesIO(), io.BytesIO()
            proc.stdout = io.BytesIO(b'EASYREAD_REMOTE ' + line + b'\n')
            with patch.object(remote_transport.subprocess, 'Popen', return_value=proc):
                with self.assertRaises(WorkspaceError) as error:
                    connection.start()
            self.assertEqual(error.exception.code, 'remote_protocol')
            self.assertTrue(connection._closed)

    def test_mutation_loss_is_not_replayed_or_used_to_kill_unknown_task(self):
        connection = remote_transport.Connection(config())
        connection.proc = MagicMock(pid=2**30, returncode=None)
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


class WorkspaceRemoteBootstrapTest(unittest.TestCase):
    def bootstrap(self, root):
        return subprocess.run([sys.executable, '-S', '-c', remote_transport.BOOTSTRAP,
                               str(remote_transport.PROTOCOL), remote_transport.build_id(),
                               '--workspace', str(root)], cwd=root, env={**os.environ, 'PYTHONPATH': str(root)},
                              input=b'', capture_output=True, timeout=5)

    def test_bootstrap_reports_missing_package_before_importing_worker_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.bootstrap(Path(tmp))
            self.assertEqual(json.loads(result.stdout.split(b' ', 1)[1])['code'], 'remote_worker_missing')
            self.assertEqual(result.stderr, b'')
            package = Path(tmp) / 'easyread' / 'workspaces'; package.mkdir(parents=True)
            (package / 'remote_worker.py').write_text('raise RuntimeError("secret-token")')
            result = self.bootstrap(Path(tmp))
            self.assertEqual(json.loads(result.stdout.split(b' ', 1)[1])['code'], 'remote_dependencies')
            self.assertEqual(result.stderr, b'')

    def test_bootstrap_codes_build_mismatch_import_failure_and_unexpected_worker_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for dependency in ('filelock', 'mcp', 'PIL'):
                (root / (dependency + '.py')).touch()
            package = root / 'easyread' / 'workspaces'; package.mkdir(parents=True)
            (package / 'remote_transport.py').write_text('PROTOCOL=999\ndef build_id(): return "wrong"\n')
            worker = package / 'remote_worker.py'
            worker.write_text('def main(): raise RuntimeError("secret-token")\n')
            result = self.bootstrap(root)
            self.assertIn(b'remote_protocol', result.stdout)
            (package / 'remote_transport.py').write_text(f'PROTOCOL={remote_transport.PROTOCOL}\ndef build_id(): return {remote_transport.build_id()!r}\n')
            for source, expected in [('def main(): raise RuntimeError("secret-token")\n', 'remote_worker_failed'),
                                     ('import missing_secret_dependency\n', 'remote_dependencies')]:
                worker.write_text(source)
                result = self.bootstrap(root)
                self.assertIn(expected.encode(), result.stdout)
                self.assertNotIn(b'secret', result.stdout)
                self.assertEqual(result.stderr, b'')

    @unittest.skipUnless(os.name == 'posix', 'POSIX remote shell')
    def test_remote_shell_reports_unavailable_configured_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = {**config(), 'python': tmp + '/missing-python'}
            command = remote_transport.ssh_command(body, 12345)[-1]
            result = subprocess.run(['/bin/sh', '-c', command], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 127)
            self.assertEqual(json.loads(result.stdout.split(b' ', 1)[1])['code'], 'remote_python')
            self.assertEqual(result.stderr, b'')


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

    def test_installed_but_unrunnable_tool_is_not_reported_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(remote_environment.shutil, 'which', return_value=None), patch.object(remote_environment.session_cli, 'executable', return_value='/remote/bin/pi'), patch.object(remote_environment.beads_cli, 'execute', side_effect=WorkspaceError('secret-token')), patch.object(remote_environment.importlib.util, 'find_spec', return_value=object()):
            result = remote_environment.check(tmp, 'pi')
        agent = next(item for item in result['checks'] if item['name'] == 'agent')
        self.assertFalse(agent['ok'])
        self.assertTrue(agent['installed'])
        self.assertEqual(agent['stage'], 'execution')
        self.assertEqual(agent['path'], '/remote/bin/pi')
        self.assertNotIn('secret-token', agent['message'])
        self.assertIn('Found /remote/bin/pi', agent['message'])
        python = next(item for item in result['checks'] if item['name'] == 'python')
        self.assertIn(sys.executable, python['message'])

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

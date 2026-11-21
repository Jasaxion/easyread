"""Opt-in real OpenSSH transport smoke; native tools/AI are disposable fakes.

EASYREAD_TEST_SSH=1 python -m unittest tests.test_workspace_ssh_integration -v
Requires Linux sshd/ssh/ssh-keygen and the workspace Python dependencies.
Uses loopback, temporary host/user keys and private HOME/workspaces only.
"""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tests import test_workspace_http as http_fixture
from tests.test_workspace_pi import FAKE_PI
from tests.test_qa_images import picture


@unittest.skipUnless(os.environ.get('EASYREAD_TEST_SSH') == '1' and sys.platform == 'linux',
                     'opt-in Linux SSH fixture')
class SSHWorkspaceIntegration(unittest.TestCase):
    request = http_fixture.WorkspaceHTTPTest.request
    login = http_fixture.WorkspaceHTTPTest.login

    @classmethod
    def setUpClass(cls):
        cls.sshd = shutil.which('sshd') or '/usr/sbin/sshd'
        if not Path(cls.sshd).is_file() or not shutil.which('ssh-keygen') or not importlib.util.find_spec('mcp'):
            raise unittest.SkipTest('sshd, ssh-keygen and mcp runtime required')
        cls.fixture = tempfile.TemporaryDirectory(prefix='easyread-real-ssh-')
        cls.addClassCleanup(cls.fixture.cleanup)
        cls.base = Path(cls.fixture.name)
        for name in ('hostkey', 'userkey'):
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(cls.base / name)], check=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); cls.ssh_port = sock.getsockname()[1]
        import pwd
        cls.user = pwd.getpwuid(os.getuid()).pw_name
        (cls.base / 'authorized_keys').write_text((cls.base / 'userkey.pub').read_text())
        config = cls.base / 'sshd_config'
        config.write_text('\n'.join([
            f'Port {cls.ssh_port}', 'ListenAddress 127.0.0.1', f'HostKey {cls.base}/hostkey',
            f'PidFile {cls.base}/sshd.pid', f'AuthorizedKeysFile {cls.base}/authorized_keys',
            f'AllowUsers {cls.user}', 'StrictModes no', 'PasswordAuthentication no',
            'KbdInteractiveAuthentication no', 'UsePAM no', 'PermitRootLogin no',
            'AllowTcpForwarding local', 'AllowAgentForwarding no', 'X11Forwarding no',
        ]) + '\n')
        cls.daemon = subprocess.Popen([cls.sshd, '-D', '-f', str(config), '-E', str(cls.base / 'sshd.log')], start_new_session=True)
        cls.addClassCleanup(cls.stop_daemon)
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(('127.0.0.1', cls.ssh_port), timeout=.2):
                    break
            except OSError:
                if cls.daemon.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Disposable sshd failed: ' + (cls.base / 'sshd.log').read_text())
                time.sleep(.05)
        cls.known_hosts = cls.base / 'known_hosts'
        cls.host_entry = f'[127.0.0.1]:{cls.ssh_port} ' + (cls.base / 'hostkey.pub').read_text()
        cls.known_hosts.write_text(cls.host_entry)
        cls.tools = cls.base / 'tools'; cls.tools.mkdir()
        cls.client = cls.base / 'client'; cls.client.mkdir()
        cls.home = cls.base / 'remote-home'; cls.home.mkdir()
        cls.remote_root = cls.base / 'remote-workspace'; cls.remote_root.mkdir()
        cls.wrapper = cls.base / 'remote-python'
        import easyread
        repo = Path(easyread.__file__).resolve().parents[1]
        cls.wrapper.write_text('#!/bin/sh\n' + '\n'.join(
            'export ' + key + '=' + shlex.quote(str(value)) for key, value in {
                'HOME': cls.home, 'XDG_CONFIG_HOME': cls.home / '.config',
                'PATH': str(cls.tools) + ':/usr/bin:/bin', 'PYTHONPATH': repo,
            }.items()) + '\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
        cls.wrapper.chmod(0o700)
        cls.ssh_config = cls.base / 'ssh_config'
        cls.ssh_config.write_text(f'''Host fixture-alias fixture-via-jump
    HostName 127.0.0.1
    User {cls.user}
    Port {cls.ssh_port}
    IdentityFile {cls.base}/userkey
    ControlMaster auto
    ControlPersist 600
    ControlPath {cls.base}/must-not-create-master
    RemoteCommand echo must-not-run
    ForkAfterAuthentication yes
    StdinNull yes
Host fixture-via-jump
    ProxyJump fixture-jump
Host fixture-jump
    HostName 127.0.0.1
    User {cls.user}
    Port {cls.ssh_port}
    IdentityFile {cls.base}/userkey
    BatchMode yes
    StrictHostKeyChecking yes
    ForwardAgent no
Host *
    UserKnownHostsFile {cls.known_hosts}
    GlobalKnownHostsFile /dev/null
''')
        ssh = cls.client / 'ssh'
        # Inject only a disposable config, never read or modify real ~/.ssh.
        ssh.write_text(f'#!{sys.executable}\nimport os,sys\nos.execv("/usr/bin/ssh", ["ssh", "-F", '
                       f'{str(cls.ssh_config)!r}] + sys.argv[1:])\n')
        ssh.chmod(0o700)
        for name in ('bd', 'dolt', 'latexmk', 'pdflatex'):
            file = cls.tools / name
            file.write_text(f'#!{sys.executable}\nimport pathlib,sys\n'
                            'if "init" in sys.argv: pathlib.Path(".beads").mkdir(exist_ok=True)\n'
                            f'print({name!r} + " version 1.3.4")\n')
            file.chmod(0o700)
        fake = cls.tools / 'pi'
        receipt = '''question = sys.stdin.read()
images = [arg[1:] for arg in args if arg.startswith("@")]
assert all(pathlib.Path(image).is_file() for image in images)
pathlib.Path("ssh-agent-receipt.json").write_text(json.dumps({"cwd": os.getcwd(), "pid": os.getpid(), "images": images, "question": question, "local_key": os.environ.get("OPENAI_API_KEY")}))
if "wait-for-cancel" in question:
    import time
    time.sleep(90)
'''
        fake.write_text(f'#!{sys.executable}\nimport sys\n'
                        'if "--version" in sys.argv: print("pi 1.6.0"); sys.exit(0)\n' +
                        FAKE_PI.replace('question = sys.stdin.read()\n', receipt))
        fake.chmod(0o700)

    @classmethod
    def stop_daemon(cls):
        if cls.daemon.poll() is not None:
            return
        os.killpg(cls.daemon.pid, signal.SIGTERM)
        try:
            cls.daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.daemon.kill(); cls.daemon.wait(timeout=5)

    def setUp(self):
        self.transport_env = patch.dict(os.environ, {
            'PATH': str(self.client) + os.pathsep + os.environ['PATH'],
            'OPENAI_API_KEY': 'LOCAL-ONLY-SENTINEL-DO-NOT-FORWARD',
            'EASYREAD_OPEN_API': '1', 'EASYREAD_MCP': '1',
        })
        self.transport_env.start(); self.addCleanup(self.transport_env.stop)
        http_fixture.WorkspaceHTTPTest.setUp(self)
        self.addCleanup(lambda: http_fixture.WorkspaceHTTPTest.tearDown(self))
        self.login()

    def api(self, path, body=None, **kwargs):
        status, headers, data = self.request('/api/workspaces' + path, body, **kwargs)
        self.assertLess(status, 400, data)
        return data

    def wait_session(self, pid, sid):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            record = self.api(f'/{pid}/sessions/{sid}')['session']
            if record['state'] not in ('running', 'queued'):
                return record
            time.sleep(.05)
        self.fail('Remote fake agent did not settle')

    def test_real_ssh_alias_inheritance_proxyjump_and_startup_diagnostics(self):
        from easyread.workspaces import WorkspaceError, remote_transport
        from easyread.workspaces.remote_config import validate
        base = {'name': 'Alias', 'host': 'fixture-alias', 'workspace': str(self.remote_root),
                'python': str(self.wrapper), 'port': None, 'user': '', 'identity_file': ''}
        for host in ('fixture-alias', 'fixture-via-jump'):
            connection = remote_transport.Connection(validate({**base, 'host': host}))
            try:
                self.assertEqual(connection.json('GET', '/status')['protocol'], remote_transport.PROTOCOL)
                self.assertTrue(connection.connected)
                self.assertFalse((self.base / 'must-not-create-master').exists())
                second = remote_transport.Connection(validate({**base, 'host': host}))
                with self.assertRaises(WorkspaceError) as error:
                    second.start()
                self.assertEqual(error.exception.code, 'remote_busy')
                self.assertTrue(second._closed)
            finally:
                connection.close()
        # The same strict SSH login reaches a Python with no EasyRead package.
        bare = self.base / 'bare-python'
        bare.write_text('#!/bin/sh\ncd ' + shlex.quote(str(self.base)) + '\nunset PYTHONPATH\nexec ' +
                        shlex.quote(sys.executable) + ' -S "$@"\n')
        bare.chmod(0o700)
        self.addCleanup(bare.unlink)
        for python, expected in ((str(bare), 'remote_worker_missing'),
                                 (str(self.base / 'missing-python'), 'remote_python')):
            connection = remote_transport.Connection(validate({**base, 'python': python}))
            with self.assertRaises(WorkspaceError) as error:
                connection.start()
            self.assertEqual(error.exception.code, expected)
            self.assertTrue(connection._closed)
        import easyread
        repo = Path(easyread.__file__).resolve().parents[1]
        bare.write_text('#!/bin/sh\nexport PYTHONPATH=' + shlex.quote(str(repo)) + '\nexec ' +
                        shlex.quote(sys.executable) + ' -S "$@"\n')
        connection = remote_transport.Connection(validate({**base, 'python': str(bare)}))
        with self.assertRaises(WorkspaceError) as error:
            connection.start()
        self.assertEqual(error.exception.code, 'remote_dependencies')
        # Explicit fields override the alias (except additive OpenSSH identities).
        connection = remote_transport.Connection(validate({**base, 'user': 'nonexistent-easyread-fixture-user'}))
        with self.assertRaises(WorkspaceError) as error:
            connection.start()
        self.assertEqual(error.exception.code, 'remote_auth')
        self.known_hosts.write_text('')
        try:
            connection = remote_transport.Connection(validate(base))
            with self.assertRaises(WorkspaceError) as error:
                connection.start()
            self.assertEqual(error.exception.code, 'remote_host_key')
            self.assertEqual(self.known_hosts.read_text(), '', 'no automatic host key acceptance')
        finally:
            self.known_hosts.write_text(self.host_entry)

    @unittest.skipUnless(shutil.which('ttyd'), 'real ttyd required on PATH for SSH terminal smoke')
    def test_real_ssh_embedded_terminal_input_resize_and_ownership(self):
        from tests.test_workspace_remote_terminal_ttyd import PROGRAM, send_frame, frame
        from tests.test_workspace_remote_terminal_http import RemoteTerminalHTTPTest
        original = self.wrapper.read_text()
        self.wrapper.write_text(original.replace('#!/bin/sh\n', '#!/bin/sh\nexport EASYREAD_TERMINAL=1\n', 1))
        self.addCleanup(lambda: self.wrapper.write_text(original))
        ttyd = self.tools / 'ttyd'
        ttyd.write_text('#!/bin/sh\nexec ' + shlex.quote(shutil.which('ttyd')) + ' "$@"\n')
        ttyd.chmod(0o700); self.addCleanup(ttyd.unlink)
        fake = self.tools / 'pi'; original_pi = fake.read_text()
        interactive = PROGRAM.replace('state = sys.argv[1]', 'state = "ssh-pty-state.json"')
        marker = 'import json, os, pathlib, sys\n'
        fake.write_text(original_pi.replace(marker, marker + 'if "--mode" not in sys.argv:\n    exec(' + repr(interactive) + ')\n    sys.exit(0)\n', 1))
        self.addCleanup(lambda: fake.write_text(original_pi))
        with patch.dict(os.environ, {'EASYREAD_TERMINAL': '1'}):
            server = self.api('/servers/create', {'name': 'Real SSH TUI', 'host': '127.0.0.1',
                'user': self.user, 'port': self.ssh_port, 'workspace': str(self.remote_root),
                'identity_file': str(self.base / 'userkey'), 'python': str(self.wrapper)})['server']
            project = self.api('/create', {'name': 'SSH terminal smoke', 'engine': 'pi', 'server_id': server['id']})['project']
            pid = project['id']; prefix = '/' + pid
            root = self.remote_root / 'workspace' / pid
            self.assertTrue(self.api(prefix)['capabilities']['terminal']['available'])
            session = self.api(prefix + '/sessions/create', {'engine': 'pi'})['session']
            endpoint = '/api/workspaces' + prefix + '/sessions/' + session['id']
            status, _, opened = self.request(endpoint + '/terminal', {})
            self.assertEqual(status, 200, opened)
            self.assertEqual(self.request(opened['url'])[0], 200)
            self.assertEqual(self.request(opened['url'], auth=False)[0], 401)
            denied, headers = RemoteTerminalHTTPTest.ws(self, opened['url'], origin='http://evil.example')
            self.assertIn(b'403', headers.split(b'\r\n')[0]); denied.close()
            sock, headers = RemoteTerminalHTTPTest.ws(self, opened['url'])
            self.assertIn(b'101', headers.split(b'\r\n')[0])
            self.assertIn(b'sec-websocket-protocol: tty', headers.lower())
            send_frame(sock, json.dumps({'AuthToken': '', 'columns': 80, 'rows': 24}).encode())
            output = b''
            for _ in range(20):
                output += frame(sock)
                if b'REMOTE-PTY-READY' in output: break
            self.assertIn(b'REMOTE-PTY-READY', output)
            state_path = root / 'ssh-pty-state.json'
            def state(columns=None):
                until = time.monotonic() + 5
                while time.monotonic() < until:
                    try:
                        result = json.loads(state_path.read_text())
                        if columns is None or result['columns'] == columns: return result
                    except (FileNotFoundError, ValueError): pass
                    time.sleep(.02)
                self.fail('Remote PTY did not report resize')
            native_pid = state()['pid']
            send_frame(sock, b'1' + json.dumps({'columns': 117, 'rows': 39}).encode())
            self.assertEqual(state(117)['rows'], 39)
            payload = b'ssh-input\x1b[A\x03\x00\x7f-end'
            send_frame(sock, b'0' + payload)
            output = b''
            for _ in range(20):
                output += frame(sock)
                if payload in output: break
            self.assertIn(payload, output)
            self.assertEqual(self.request(endpoint + '/send', {'message': 'must not race terminal'})[0], 409)
            self.assertEqual(self.request(endpoint + '/terminal', {})[2]['url'], opened['url'])
            self.assertEqual(state()['pid'], native_pid)
            self.assertEqual(self.request('/api/workspaces/servers/' + server['id'] + '/disconnect', {})[0], 409)
            sock.close()
            until = time.monotonic() + 8
            while self.app.workspaces.remotes.terminals.entries and time.monotonic() < until:
                time.sleep(.03)
            self.assertFalse(self.app.workspaces.remotes.terminals.entries)
            record = self.request(endpoint)[2]['session']
            self.assertFalse(record['terminal_open']); self.assertEqual(record['native_id'], session['native_id'])
            proc = Path(f'/proc/{native_pid}/stat')
            self.assertTrue(not proc.exists() or proc.read_text().split()[2] == 'Z', 'remote PTY process survived detach')
            self.api(prefix + '/delete', {})
            self.api('/servers/' + server['id'] + '/delete', {})

    def test_real_ssh_remote_routing_images_cancel_ranges_full_api_and_host_keys(self):
        server = self.api('/servers/create', {'name': 'Isolated SSH fixture', 'host': '127.0.0.1',
            'user': self.user, 'port': self.ssh_port, 'workspace': str(self.remote_root),
            'identity_file': str(self.base / 'userkey'), 'python': str(self.wrapper)})['server']
        sid = server['id']
        check = self.api(f'/servers/{sid}/check', {'engine': 'pi'})
        self.assertTrue(check['ok'], check)
        # Missing mandatory prerequisite must reject project creation freshly.
        tool = self.tools / 'dolt'; tool.rename(self.tools / 'dolt-disabled')
        try:
            missing = self.api(f'/servers/{sid}/check', {'engine': 'pi'})
            self.assertFalse(missing['ok'])
            self.assertEqual(self.request('/api/workspaces/create', {'name': 'must fail', 'engine': 'pi', 'server_id': sid})[0], 409)
        finally:
            (self.tools / 'dolt-disabled').rename(tool)
        project = self.api('/create', {'name': 'SSH smoke', 'description': 'Real transport, fake native tools', 'engine': 'pi', 'server_id': sid})['project']
        pid = project['id']; prefix = '/' + pid
        root = self.remote_root / 'workspace' / pid
        self.assertTrue((root / 'README.md').is_file())
        self.assertFalse((Path(self.tmp.name) / 'workspace' / pid).exists(), 'no local shadow workspace')
        detail = self.api(prefix)
        self.assertEqual(detail['workspace_root'], str(root)); self.assertFalse(detail['capabilities']['terminal']['available'])
        self.api(prefix + '/files/create', {'path': 'remote.txt', 'content': 'remote-only'})
        self.assertEqual(self.api(prefix + '/file?path=remote.txt')['content'], 'remote-only')
        paper = Path(self.tmp.name) / 'ssh-paper'; paper.mkdir()
        def update_paper(text):
            (paper / 'paper.json').write_text(json.dumps({'meta': {'title_en': 'SSH API paper'},
                'blocks': [{'id': 'b1', 'type': 'paragraph', 'en': text}]}))
        update_paper('live-evidence-original')
        self.api(prefix + '/papers/link', {'document_id': 'ssh-paper'})
        self.assertFalse((root / '.easyread-paper-snapshots.json').exists())
        self.assertEqual(json.loads((root / '.easyread-papers.json').read_text()), {'papers': ['ssh-paper']})
        unlinked = Path(self.tmp.name) / 'ssh-unlinked'; unlinked.mkdir()
        (unlinked / 'paper.json').write_text(json.dumps({'meta': {'title_en': 'Full library access'}, 'blocks': []}))
        # Standard user-configured HTTP MCP using the full existing access code,
        # run on the SSH host. No private bridge, restricted token or snapshot.
        program = f'''import asyncio, httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
async def main():
    async with httpx.AsyncClient(headers={{"Authorization":"Bearer test-workspace-code"}}, trust_env=False) as http:
        async with streamable_http_client("http://127.0.0.1:{self.port}/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print(await session.call_tool("get_content", {{"document_id":"ssh-paper"}}))
                print(await session.call_tool("get_document", {{"document_id":"ssh-unlinked"}}))
asyncio.run(main())
'''
        def remote_read():
            return subprocess.check_output(['/usr/bin/ssh', '-F', os.devnull, '-o', 'BatchMode=yes',
                '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={self.known_hosts}',
                '-o', 'GlobalKnownHostsFile=/dev/null', '-i', str(self.base / 'userkey'), '-p', str(self.ssh_port),
                '-l', self.user, '127.0.0.1', shlex.join([str(self.wrapper), '-c', program])], timeout=20)
        output = remote_read()
        self.assertIn(b'live-evidence-original', output)
        self.assertIn(b'ssh-unlinked', output)
        update_paper('live-evidence-revised-without-relink')
        output = remote_read()
        self.assertIn(b'live-evidence-revised-without-relink', output)
        data = b'%PDF-1.4\nfixture-range-payload'
        self.api(prefix + '/files/upload?path=preview.pdf', data, raw=True)
        status, headers, part = self.request('/api/workspaces' + prefix + '/raw?path=preview.pdf', headers={'Range': 'bytes=0-7'})
        self.assertEqual(status, 206); self.assertEqual(part, data[:8]); self.assertIn('Content-Range', headers)
        status, headers, part = self.request('/api/workspaces' + prefix + '/raw?path=preview.pdf', method='HEAD')
        self.assertEqual(status, 200); self.assertEqual(part, b''); self.assertEqual(int(headers['Content-Length']), len(data))
        record = self.api(prefix + '/sessions/create', {'engine': 'pi', 'model': 'provider/model'})['session']
        session = record['id']
        self.api(prefix + f'/sessions/{session}/send', {'message': 'inspect pasted image', 'images': [picture()]})
        record = self.wait_session(pid, session)
        self.assertEqual(record['state'], 'idle', record.get('error'))
        receipt = json.loads((root / 'ssh-agent-receipt.json').read_text())
        self.assertEqual(receipt['cwd'], str(root)); self.assertIsNone(receipt['local_key'])
        self.assertEqual(len(receipt['images']), 1); self.assertTrue(receipt['images'][0].startswith(str(root / 'chat-media')))
        self.assertTrue(any(message.get('images') for message in record['messages']))
        # Reuse native identity, exercise simultaneous reads and cancellation.
        self.api(prefix + f'/sessions/{session}/send', {'message': 'wait-for-cancel'})
        self.assertEqual(self.request(f'/api/workspaces/servers/{sid}/disconnect', {})[0], 409)
        with ThreadPoolExecutor(max_workers=3) as pool:
            reads = [pool.submit(self.api, prefix + '/tree'), pool.submit(self.api, prefix + '/sessions')]
            cancelled = self.api(prefix + f'/sessions/{session}/cancel', {})
            for read in reads: read.result(timeout=10)
        self.assertIn(cancelled['session']['state'], ('interrupted', 'idle'))
        # Killing the SSH client simulates a lost transport, not a graceful API cancel.
        receipt_path = root / 'ssh-agent-receipt.json'; receipt_path.unlink()
        self.api(prefix + f'/sessions/{session}/send', {'message': 'wait-for-cancel EOF cleanup'})
        deadline = time.monotonic() + 8
        while not receipt_path.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        native_pid = json.loads(receipt_path.read_text())['pid']
        connection = self.app.workspaces.remotes.connections[sid]
        connection.proc.kill(); connection.proc.wait(timeout=3)
        proc_stat = Path(f'/proc/{native_pid}/stat')
        while time.monotonic() < deadline:
            if not proc_stat.exists() or proc_stat.read_text().split()[2] == 'Z':
                break
            time.sleep(.05)
        else:
            self.fail('Managed remote agent survived SSH EOF')
        # Read-only reconnect/reconciliation retains messages, never repeats send.
        restored_after_eof = self.api(prefix + f'/sessions/{session}')['session']
        self.assertEqual(restored_after_eof['state'], 'interrupted')
        self.api(f'/servers/{sid}/disconnect', {})
        status, _, error = self.request('/api/workspaces' + prefix + '/tree')
        self.assertEqual((status, error['code']), (409, 'remote_disconnected'))
        self.assertTrue(any(p['id'] == pid for p in self.api('')['projects']))
        # Replace only our disposable trust file: unknown host key must fail closed.
        self.known_hosts.write_text('')
        try:
            status, _, _ = self.request(f'/api/workspaces/servers/{sid}/connect', {})
            self.assertGreaterEqual(status, 400)
        finally:
            self.known_hosts.write_text(self.host_entry)
        self.api(f'/servers/{sid}/connect', {})
        restored = self.api(prefix + f'/sessions/{session}')['session']
        self.assertEqual(restored['native_id'], record['native_id']); self.assertTrue(restored['messages'])
        self.api(prefix + '/delete', {})
        self.assertFalse(root.exists()); self.api(f'/servers/{sid}/delete', {})

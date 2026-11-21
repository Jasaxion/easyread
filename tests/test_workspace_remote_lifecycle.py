"""Exercise worker shutdown with active fake Agent/TeX process trees, never SSH/providers."""
import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from easyread.workspaces.remote_snapshots import SnapshotStore
from easyread.workspaces.remote_transport import PROTOCOL, build_id


FAKE_TOOL = r'''
import json, os, pathlib, signal, subprocess, sys, time
args = sys.argv[1:]
if '--version' in args or '--list-models' in args:
    print('fake tool 1.0')
    raise SystemExit(0)
if '--session-id' in args:
    def arg(key): return args[args.index(key) + 1]
    native_id = arg('--session-id')
    path = pathlib.Path(arg('--session-dir')) / ('2026-01-01T00-00-00-000Z_' + native_id + '.jsonl')
    row = {'type': 'session', 'version': 3, 'id': native_id, 'cwd': os.getcwd()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(row) + '\n')
    print(json.dumps(row), flush=True)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)', 'easyread-remote-active-test'])
def stop(*unused):
    if child.poll() is None:
        child.terminate()
    child.wait(timeout=5)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
path = (pathlib.Path(__file__).parent / 'processes') / (pathlib.Path(sys.argv[0]).name + '.json')
path.write_text(json.dumps({'parent': os.getpid(), 'child': child.pid}))
time.sleep(120)
'''


@unittest.skipUnless(os.name == 'posix', 'POSIX process-group shutdown integration')
class WorkspaceRemoteActiveLifecycleTest(unittest.TestCase):
    def test_eof_and_termination_stop_active_agent_latex_trees_and_preserve_history(self):
        for shutdown in ('eof', 'sigterm'):
            with self.subTest(shutdown=shutdown), tempfile.TemporaryDirectory(prefix='easyread-remote-active-') as temporary:
                base = Path(temporary)
                workspace, home, processes = (base / name for name in ('workspace-root', 'home', 'processes'))
                for path in (workspace, home, processes):
                    path.mkdir()
                tools = {}
                for name in ('fake-pi', 'fake-pdflatex'):
                    tool = base / name
                    tool.write_text('#!' + sys.executable + '\n' + FAKE_TOOL)
                    tool.chmod(0o700)
                    tools[name] = str(tool)
                project = SnapshotStore(workspace).allocate({'name': 'Active shutdown', 'engine': 'pi'})
                prefix = '/api/workspaces/' + project['id']
                env = {key: value for key, value in os.environ.items() if key in ('PATH', 'SYSTEMROOT', 'LANG', 'LC_ALL')}
                env.update(HOME=str(home), USERPROFILE=str(home), EASYREAD_HOME=str(home),
                           EASYREAD_WORKSPACE_PI_COMMAND=tools['fake-pi'],
                           EASYREAD_TEX_PDFLATEX=tools['fake-pdflatex'])
                token = 'private-test-token-for-active-worker-lifecycle'
                worker = None
                owned_pids = []

                def start():
                    proc = subprocess.Popen([sys.executable, '-m', 'easyread.workspaces.remote_worker', '--workspace', str(workspace)],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                    proc.stdin.write((json.dumps({'token': token, 'protocol': PROTOCOL, 'build': build_id()}) + '\n').encode())
                    proc.stdin.flush()
                    line = proc.stdout.readline(4096)
                    self.assertTrue(line.startswith(b'EASYREAD_REMOTE '), line)
                    return proc, json.loads(line[len(b'EASYREAD_REMOTE '):])['port']

                def request(path, body=None):
                    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
                    try:
                        conn.request('GET' if body is None else 'POST', path,
                                     json.dumps(body).encode() if body is not None else None,
                                     {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
                        response = conn.getresponse()
                        result = json.loads(response.read())
                        self.assertEqual(response.status, 200, result)
                        return result
                    finally:
                        conn.close()

                def close_pipes(proc):
                    for stream in (proc.stdin, proc.stdout, proc.stderr):
                        stream.close()

                try:
                    worker, port = start()
                    session = request(prefix + '/sessions/create', {'engine': 'pi'})['session']
                    sid, native_id = session['id'], session['native_id']
                    request(prefix + '/sessions/' + sid + '/send', {'message': 'Keep this accepted turn in history'})
                    request(prefix + '/latex')
                    request(prefix + '/latex/compile', {})
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and len(list(processes.glob('*.json'))) != 2:
                        time.sleep(.05)
                    self.assertEqual(len(list(processes.glob('*.json'))), 2, request(prefix + '/sessions/' + sid))
                    for path in processes.glob('*.json'):
                        owned_pids.extend(json.loads(path.read_text()).values())
                    self.assertTrue(request('/status')['busy'])
                    if shutdown == 'eof':
                        worker.stdin.close()
                    else:
                        worker.send_signal(signal.SIGTERM)
                    worker.wait(timeout=20)
                    self.assertEqual(worker.returncode, 0, worker.stderr.read().decode())
                    for pid in owned_pids:
                        with self.assertRaises(ProcessLookupError, msg=f'worker left process {pid} alive'):
                            os.kill(pid, 0)
                    owned_pids.clear()
                    close_pipes(worker)
                    worker, port = start()
                    restored = request(prefix + '/sessions/' + sid)['session']
                    self.assertEqual(restored['native_id'], native_id)
                    self.assertNotEqual(restored['state'], 'running')
                    history = request(prefix + '/sessions/' + sid + '/history')['messages']
                    self.assertTrue(any('Keep this accepted turn' in row.get('content', '') for row in history))
                    self.assertFalse(request('/status')['busy'])
                finally:
                    if worker:
                        if worker.poll() is None:
                            worker.stdin.close()
                            try:
                                worker.wait(timeout=20)
                            except subprocess.TimeoutExpired:
                                worker.kill()
                                worker.wait(timeout=5)
                        close_pipes(worker)
                    for pid in owned_pids:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass


if __name__ == '__main__':
    unittest.main()

"""Real HTTP on both sides of the SSH boundary, with an in-process test tunnel.

No SSH host, provider credential, real agent or production library is touched.
The transport unit tests separately assert strict OpenSSH construction/lifecycle.
"""
from contextlib import contextmanager
import http.client
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from easyread.server import workspaces_api
from easyread.workspaces import WorkspaceError, remote, remote_worker, remote_transport
from easyread.workspaces.remote_config import identifier
from easyread.workspaces.remote_papers import ReferenceStore
from tests import test_workspace_http as local_http


READY = {'ok': True, 'checks': [], 'capabilities': {'remote': True}, 'checked_at': '2026-01-01T00:00:00Z'}


class WorkspaceRemoteHTTPTest(unittest.TestCase):
    setUp_local = local_http.WorkspaceHTTPTest.setUp
    tearDown_local = local_http.WorkspaceHTTPTest.tearDown
    request = local_http.WorkspaceHTTPTest.request
    login = local_http.WorkspaceHTTPTest.login

    def setUp(self):
        self.setUp_local()
        self.remote_tmp = tempfile.TemporaryDirectory(prefix='easyread-remote-worker-')
        self.remote_root = Path(self.remote_tmp.name)
        self.worker = remote_worker.WorkerServer(self.remote_root, 'test-token-that-is-private-to-the-worker')
        self.worker_thread = threading.Thread(target=self.worker.serve_forever, daemon=True)
        self.worker_thread.start()
        self.connections = []
        case = self

        class DirectConnection(remote_transport.Connection):
            def __init__(self, config):
                super().__init__(config)
                self.token = case.worker.token
                self.starts = 0
                case.connections.append(self)

            @property
            def connected(self):
                return bool(self.remote_port and not self._closed)

            def start(self):
                if self._closed:
                    raise remote_transport.unavailable()
                if not self.connected:
                    self.starts += 1
                    self.remote_port = case.worker.server_port

            def close(self):
                with self.lock:
                    self._closed = True
                    for conn in self.pool:
                        conn.close()
                    self.pool.clear()

        self.direct = patch.object(remote, 'Connection', DirectConnection)
        self.direct.start()
        self.http_patch = patch.object(remote_transport, 'TunnelHTTP',
                                      side_effect=lambda proxy, port: http.client.HTTPConnection('127.0.0.1', port, timeout=10))
        self.http_patch.start()
        self.check_patch = patch.object(remote_worker, 'check', return_value=READY)
        self.check_patch.start()
        self.login()
        self.manager = self.app.workspaces.remotes
        status, _, result = self.request('/api/workspaces/servers/create', {
            'name': 'Test remote', 'host': '127.0.0.1', 'user': 'research', 'port': 22,
            'workspace': str(self.remote_root), 'python': 'python3'})
        self.assertEqual(status, 200, result)
        self.sid = result['server']['id']

    def tearDown(self):
        self.manager.close()
        self.worker.shutdown()
        self.worker.close_clients()
        self.worker.server_close()
        self.worker.app.workspaces.close()
        self.worker_thread.join(5)
        self.check_patch.stop()
        self.http_patch.stop()
        self.direct.stop()
        self.remote_tmp.cleanup()
        self.tearDown_local()

    def allocated(self, sid=None):
        project = self.worker.app.workspaces.store.allocate({'name': 'Remote project', 'description': 'Remote only'})
        with self.manager.registry.transaction() as data:
            config = data['servers'][sid or self.sid]
            data['projects'][project['id']] = self.manager.decorate(project, config)
        return project['id'], '/api/workspaces/' + project['id']

    def test_server_crud_offline_listing_private_registry_and_setup_script(self):
        self.assertEqual(self.connections, [])
        self.assertEqual(self.request('/api/workspaces/servers')[2]['servers'][0]['connected'], False)
        self.assertEqual(self.connections, [])
        if os.name != 'nt':
            self.assertEqual(self.manager.registry.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.manager.registry.directory.stat().st_mode & 0o777, 0o700)
        status, headers, script = self.request('/api/workspaces/servers/setup-script')
        self.assertEqual(status, 200)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertIn(b'StrictHostKeyChecking=yes', script)
        self.assertIn(b'install_workspace_deps.py --install', script)
        self.assertNotIn(self.worker.token.encode(), script)
        status, _, _ = self.request('/api/workspaces/servers/' + self.sid + '/update', {'name': 'Renamed'})
        self.assertEqual(status, 200)
        self.assertEqual(self.request('/api/workspaces/servers/' + self.sid + '/delete', {})[0], 200)
        self.assertEqual(self.request('/api/workspaces/servers')[2]['servers'], [])

    def test_server_optional_ssh_fields_round_trip_and_explicit_values_are_preserved(self):
        status, _, result = self.request('/api/workspaces/servers/create', {
            'name': 'Alias', 'host': 'research_alias', 'workspace': str(self.remote_root)})
        self.assertEqual(status, 200, result)
        server = result['server']; sid = server['id']
        self.assertIsNone(server['port'])
        self.assertEqual((server['user'], server['identity_file']), ('', ''))
        explicit = self.request('/api/workspaces/servers/' + self.sid + '/update', {'name': 'Renamed'})[2]['server']
        self.assertEqual((explicit['user'], explicit['port']), ('research', 22))
        updated = self.request('/api/workspaces/servers/' + self.sid + '/update', {'user': '', 'port': None})[2]['server']
        self.assertEqual(updated['user'], '')
        self.assertIsNone(updated['port'])
        self.assertEqual(self.request('/api/workspaces/servers/' + sid + '/delete', {})[0], 200)

    def test_startup_check_reports_typed_failure_and_configured_python_without_secrets(self):
        failure = remote_transport.startup_error('remote_dependencies', {'python': '/remote/venv/bin/python'})
        self.request('/api/workspaces/servers/' + self.sid + '/update', {'python': '/remote/venv/bin/python'})
        with patch.object(remote, 'Connection', side_effect=failure):
            status, _, report = self.request('/api/workspaces/servers/' + self.sid + '/check', {'engine': 'pi'})
        self.assertEqual(status, 200)
        self.assertEqual(report['code'], 'remote_dependencies')
        self.assertEqual(report['configured_python'], '/remote/venv/bin/python')
        self.assertEqual(report['checks'][0]['stage'], 'worker')
        self.assertIn('matching EasyRead', report['checks'][0]['message'])
        self.assertEqual(self.manager.registry.server(self.sid)['last_check'], report)

    def test_server_routes_require_access_and_csrf(self):
        self.assertEqual(self.request('/api/workspaces/servers', auth=False)[0], 401)
        self.assertEqual(self.request('/api/workspaces/servers/setup-script', auth=False)[0], 401)
        self.token = ''
        self.assertEqual(self.request('/api/workspaces/servers/' + self.sid + '/connect', {})[0], 403)
        self.assertEqual(self.connections, [])

    def test_remote_routes_never_resolve_local_root_and_reuse_one_transport(self):
        pid, prefix = self.allocated()
        with patch.object(self.app.workspaces.store, 'root', side_effect=AssertionError('local fallback')):
            self.assertEqual(self.request(prefix + '/files/create', {'path': 'remote.txt', 'content': 'remote only'})[0], 200)
            self.assertEqual(self.request(prefix + '/file?path=remote.txt')[2]['content'], 'remote only')
            self.assertEqual(self.request(prefix)[2]['workspace_root'], str(self.remote_root / 'workspace' / pid))
            self.assertTrue(self.request(prefix)[2]['capabilities']['remote'])
            self.assertEqual(self.request(prefix + '/update', {'name': 'New name', 'server_id': 'wrong-host'})[0], 200)
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(self.connections[0].starts, 1)
        self.assertFalse((Path(self.tmp.name) / 'workspace' / pid).exists())
        self.assertEqual(self.request('/api/workspaces')[2]['projects'][0]['name'], 'New name')
        self.assertEqual(self.request('/api/workspaces/servers/' + self.sid + '/update', {'host': 'other.example'})[2]['code'], 'remote_in_use')
        self.assertEqual(self.request('/api/workspaces/servers/' + self.sid + '/delete', {})[2]['code'], 'remote_in_use')

    def test_disconnect_is_persistent_no_auto_reconnect_and_busy_refused(self):
        pid, prefix = self.allocated()
        self.request(prefix)
        with patch.object(self.worker.app.workspaces, 'busy', return_value=True):
            status, _, body = self.request('/api/workspaces/servers/' + self.sid + '/disconnect', {})
            self.assertEqual((status, body['code']), (409, 'remote_busy'))
        self.assertTrue(self.connections[0].connected)
        self.assertEqual(self.request('/api/workspaces/servers/' + self.sid + '/disconnect', {})[0], 200)
        self.assertTrue(self.manager.registry.server(self.sid)['suspended'])
        for route, body in ((prefix, None), (prefix + '/files/create', {'path': 'never'})):
            status, _, result = self.request(route, body)
            self.assertEqual((status, result['code']), (409, 'remote_disconnected'))
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(self.request('/api/workspaces')[2]['projects'][0]['id'], pid)
        self.assertEqual(self.request('/api/workspaces/servers/' + self.sid + '/connect', {})[0], 200)
        self.assertEqual(self.request(prefix)[0], 200)
        self.assertEqual(len(self.connections), 2)

    def test_failed_busy_probe_does_not_close_and_active_request_protects_disconnect(self):
        _, prefix = self.allocated()
        self.request(prefix)
        conn = self.connections[0]
        with patch.object(conn, 'json', side_effect=remote_transport.unavailable()):
            self.assertEqual(self.request('/api/workspaces/servers/' + self.sid + '/disconnect', {})[0], 503)
        self.assertTrue(conn.connected)
        self.assertFalse(self.manager.registry.server(self.sid)['suspended'])
        with self.manager.connection(self.sid):
            self.assertEqual(self.request('/api/workspaces/servers/' + self.sid + '/disconnect', {})[2]['code'], 'remote_busy')
        self.manager.disconnect(self.sid, suspend=False)
        self.assertFalse(self.manager.registry.server(self.sid)['suspended'])

    def test_missing_environment_fresh_create_and_failed_init_cleanup(self):
        status, _, checked = self.request('/api/workspaces/servers/' + self.sid + '/check', {'engine': 'claude'})
        self.assertEqual((status, checked['ok']), (200, True))
        failed = {'ok': False, 'checks': [{'name': 'dolt', 'ok': False, 'required': True, 'message': 'Install Dolt'}]}
        self.check_patch.stop()
        with patch.object(remote_worker, 'check', return_value=failed):
            report = self.request('/api/workspaces/servers/' + self.sid + '/check', {'engine': 'claude'})[2]
            self.assertFalse(report['ok'])
            self.assertTrue(report['checks'][0]['ok'], 'worker available does not imply tools are ready')
            self.assertEqual(report['checks'][1:], failed['checks'])
            self.assertEqual(report['configured_python'], 'python3')
            status, _, body = self.request('/api/workspaces/create', {'name': 'Blocked', 'server_id': self.sid})
            self.assertEqual((status, body['code']), (409, 'remote_environment'))
        self.check_patch.start()
        # Existing local test env intentionally points bd to a missing command.
        status, _, body = self.request('/api/workspaces/create', {'name': 'Failed init', 'server_id': self.sid})
        self.assertEqual((status, body['code']), (409, 'remote_environment'))
        self.assertEqual(self.manager.registry.listing(), [])
        self.assertEqual(self.worker.app.workspaces.store.listing(), [])
        self.assertEqual(list((self.remote_root / 'workspace').iterdir()), [])

    def test_successful_creation_and_ambiguous_creation_retains_fixed_reference(self):
        original = ReferenceStore.create

        def initialized(store, body):
            result = original(store, body)
            project = result['project']
            project['initialization'] = {'git': {'ok': True}, 'beads': {'ok': True}}
            from easyread.library.store import write_json_atomic
            write_json_atomic(store.meta(project['id']) / 'project.json', project)
            return result

        with patch.object(ReferenceStore, 'create', initialized):
            status, _, body = self.request('/api/workspaces/create', {'name': 'Created remote', 'server_id': self.sid, 'engine': 'pi'})
        self.assertEqual(status, 200, body)
        self.assertEqual(body['project']['engine'], 'pi')
        self.assertNotIn('pending', body['project']['remote'])
        connection = self.connections[0]
        real_json = connection.json

        def lost(method, path, data=None):
            if path.endswith('/create'):
                # The worker allocated it but the response never reached the client.
                self.worker.app.workspaces.store.allocate(data)
                raise remote_transport.unavailable()
            return real_json(method, path, data)

        with patch.object(connection, 'json', side_effect=lost) as calls:
            self.assertEqual(self.request('/api/workspaces/create', {'name': 'Ambiguous', 'server_id': self.sid})[0], 503)
            self.assertEqual(sum(call.args[1].endswith('/create') for call in calls.call_args_list), 1)
        project = next(p for p in self.manager.registry.listing() if p['name'] == 'Ambiguous')
        self.assertTrue(project['remote']['pending'])
        self.assertEqual(self.request('/api/workspaces/' + project['id'])[0], 200)
        self.assertNotIn('pending', self.manager.registry.project(project['id'])['remote'])

    def test_creation_reserves_server_target_and_rejects_concurrent_deletion(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        outcomes = {}
        def checking(*args):
            started.set()
            if not release.wait(5):
                raise RuntimeError('test release timed out')
            return READY
        def initialized(store, body):
            project = store.allocate(body)
            project['initialization'] = {'git': {'ok': True}, 'beads': {'ok': True}}
            from easyread.library.store import write_json_atomic
            write_json_atomic(store.meta(project['id']) / 'project.json', project)
            return {'project': project}
        def creating():
            outcomes['create'] = self.request('/api/workspaces/create', {'name': 'Racing project', 'server_id': self.sid})
        def deleting():
            outcomes['delete'] = self.request('/api/workspaces/servers/' + self.sid + '/delete', {})
            finished.set()
        with patch.object(remote_worker, 'check', side_effect=checking), patch.object(ReferenceStore, 'create', initialized):
            creator = threading.Thread(target=creating)
            deleter = threading.Thread(target=deleting)
            creator.start()
            self.assertTrue(started.wait(5))
            deleter.start()
            self.assertTrue(finished.wait(2), 'server deletion must fail promptly, not hold a global lock')
            release.set()
            creator.join(10)
            deleter.join(10)
        self.assertEqual(outcomes['create'][0], 200)
        self.assertEqual(outcomes['delete'][2]['code'], 'remote_busy')
        self.assertEqual(self.manager.registry.listing()[0]['remote']['server_id'], self.sid)
        self.assertEqual(self.request('/api/workspaces/servers/' + self.sid + '/delete', {})[2]['code'], 'remote_in_use')

    def test_unknown_server_never_creates_local_and_transport_check_failure_is_diagnostic(self):
        for sid in ('', None, 'not-a-server', identifier()):
            self.assertNotEqual(self.request('/api/workspaces/create', {'name': 'No fallback', 'server_id': sid})[0], 200)
        self.assertEqual(self.app.workspaces.store.listing(), [])
        with patch.object(remote, 'Connection', side_effect=remote_transport.unavailable()):
            status, _, result = self.request('/api/workspaces/servers/' + self.sid + '/check', {'engine': 'codex'})
        self.assertEqual(status, 200)
        self.assertFalse(result['ok'])
        self.assertEqual(result['checks'][0]['name'], 'ssh_worker')

    def test_binary_upload_raw_range_head_conditional_and_export_import_target(self):
        pid, prefix = self.allocated()
        raw = b'%PDF-test' + bytes(range(256)) * 1024
        self.assertEqual(self.request(prefix + '/files/upload?path=preview.pdf', raw, raw=True)[0], 200)
        self.assertEqual((self.remote_root / 'workspace' / pid / 'preview.pdf').read_bytes(), raw)
        status, headers, body = self.request(prefix + '/raw?path=preview.pdf', headers={'Range': 'bytes=5-100'})
        self.assertEqual((status, body), (206, raw[5:101]))
        self.assertEqual(headers['Content-Range'], 'bytes 5-100/' + str(len(raw)))
        status, headers, body = self.request(prefix + '/raw?path=preview.pdf', method='HEAD', headers={'Range': 'bytes=-5'})
        self.assertEqual((status, headers['Content-Length'], body), (206, '5', b''))
        self.assertEqual(self.request(prefix + '/raw?path=preview.pdf', headers={'Range': 'bytes=0-1,3-4'})[0], 416)
        self.request(prefix + '/files/create', {'path': 'main.txt', 'content': 'hello'})
        current = self.request(prefix + '/file?path=main.txt')[2]
        if 'stamp' in current:
            self.assertTrue(self.request(prefix + '/file?path=main.txt&if_stamp=' + current['stamp'])[2]['unchanged'])
        status, _, archive = self.request(prefix + '/export')
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            manifest = json.loads(bundle.read('manifest.json'))
        self.assertNotIn('remote', manifest['project'])
        self.assertNotIn(self.worker.token.encode(), archive)
        status, _, imported = self.request('/api/workspaces/import', archive, raw=True)
        self.assertEqual(status, 200, imported)
        self.assertNotIn('remote', imported['project'])
        self.assertNotEqual(imported['project']['id'], pid)
        self.assertTrue(self.app.workspaces.store.root(imported['project']['id']).is_dir())

    def test_pasted_images_and_session_history_stay_remote(self):
        pid, prefix = self.allocated()
        payload = {'message': 'look', 'images': [{'data': 'data:image/png;base64,AAAA', 'name': 'paste.png'}]}
        with patch.object(self.worker.app.workspaces.sessions, 'send', return_value={'accepted': True}) as send:
            status, _, result = self.request(prefix + '/sessions/abcdef/send', payload)
            self.assertEqual((status, result), (200, {'accepted': True}))
            send.assert_called_once_with(pid, 'abcdef', payload)
        with patch.object(self.worker.app.workspaces.sessions, 'history', return_value={'messages': [{'role': 'user', 'content': 'look', 'images': ['persisted']}]}):
            self.assertEqual(self.request(prefix + '/sessions/abcdef/history?limit=40')[2]['messages'][0]['images'], ['persisted'])
        self.assertEqual(self.request(prefix + '/sessions/abcdef/terminal', {})[2]['code'], 'remote_terminal_unavailable')

    def test_actual_pasted_png_is_validated_stored_and_journaled_only_remotely(self):
        from PIL import Image
        from easyread.workspaces import session_images
        pid, prefix = self.allocated()
        image = io.BytesIO()
        Image.new('RGB', (2, 2), 'red').save(image, 'PNG')
        uri = 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode()
        status, _, created = self.request(prefix + '/sessions/create', {'engine': 'codex'})
        self.assertEqual(status, 200, created)
        sid = created['session']['id']
        sessions = self.worker.app.workspaces.sessions

        def accepted(project_id, session_id, message, prompt, references, images):
            paths = session_images.inputs(sessions.store.root(project_id), images)
            self.assertTrue(all(Path(item['path']).is_relative_to(self.remote_root) for item in paths))
            def journal(data):
                sessions._record(data, session_id)['messages'].append({'role': 'user', 'content': message, 'images': images})
            sessions._commit(project_id, journal)
            return {'accepted': True}

        with patch.object(sessions.native, 'send', side_effect=accepted):
            status, _, result = self.request(prefix + '/sessions/' + sid + '/send', {'message': 'inspect', 'images': [uri]})
        self.assertEqual((status, result), (200, {'accepted': True}))
        history = self.request(prefix + '/sessions/' + sid + '/history')[2]
        name = history['messages'][0]['images'][0]
        self.assertEqual(self.request(prefix + '/raw?path=' + name)[2], image.getvalue())
        self.assertFalse((Path(self.tmp.name) / 'workspace' / pid).exists())
        self.assertEqual(self.request(prefix + '/sessions/' + sid + '/send', {'message': 'bad', 'images': ['data:image/png;base64,AAAA']})[0], 400)

    def test_papers_keep_references_and_full_api_reads_are_live_without_relink(self):
        from easyread.workspaces import session_cli
        from easyread.workspaces.remote_papers import LINKS_FILE
        pid, prefix = self.allocated()
        document = Path(self.tmp.name) / 'local-paper'
        document.mkdir()
        def paper(text):
            (document / 'paper.json').write_text(json.dumps({'meta': {'title_en': 'Local paper'},
                'blocks': [{'id': 'p1', 'type': 'paragraph', 'en': text}]}))
        paper('original')
        (document / 'reader.json').write_text(json.dumps({'paper_note': {'content': 'original note'}, 'notes': {}}))
        result = self.request(prefix + '/papers')[2]
        self.assertEqual(result['mode'], 'native_api')
        self.assertEqual(result['available'][0]['id'], 'local-paper')
        status, _, result = self.request(prefix + '/papers/link', {'document_id': 'local-paper'})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.manager.registry.project(pid)['papers'], ['local-paper'])
        root = self.remote_root / 'workspace' / pid
        self.assertEqual(json.loads((root / LINKS_FILE).read_text()), {'papers': ['local-paper']})
        self.assertFalse((root / '.easyread-paper-snapshots.json').exists())
        for engine in ('codex', 'claude'):
            self.assertEqual(session_cli.project_mcp_args(engine, self.remote_root, pid), [],
                             'must preserve the remote account full-library MCP, not inject snapshots')
        # Existing global Bearer access, deliberately no browser Cookie/X-Token.
        self.app.public_api.settings.update(enabled=True, mcp_enabled=True)
        headers = {'Authorization': 'Bearer test-workspace-code'}
        self.assertEqual(self.request('/api/v1/documents/local-paper/content', auth=False)[0], 401)
        def read(suffix):
            status, _, data = self.request('/api/v1/documents/local-paper/' + suffix, auth=False, headers=headers)
            self.assertEqual(status, 200, data)
            return data
        self.assertEqual(read('content')['items'][0]['en'], 'original')
        paper('latest, no relink')
        (document / 'reader.json').write_text(json.dumps({'paper_note': {'content': 'latest note'}, 'notes': {}}))
        self.assertEqual(read('content')['items'][0]['en'], 'latest, no relink')
        self.assertEqual(read('notes')['paper_note']['content'], 'latest note')
        unlinked = Path(self.tmp.name) / 'unlinked-paper'; unlinked.mkdir()
        (unlinked / 'paper.json').write_text(json.dumps({'meta': {'title_en': 'Not linked'}, 'blocks': []}))
        self.assertEqual(self.request('/api/v1/documents/unlinked-paper', auth=False, headers=headers)[0], 200,
                         'the full access code is not limited to linked papers')
        self.worker.app.workspaces.store.link(pid, 'local-paper', remove=True)
        from easyread.library.library import Library
        with patch.object(Library, 'all', side_effect=AssertionError('linked_only scanned candidates')):
            self.assertEqual(self.request(prefix + '/papers?linked_only=1')[2]['linked'], [])
        self.assertEqual(self.manager.registry.project(pid)['papers'], [])
        self.assertEqual(read('content')['items'][0]['en'], 'latest, no relink',
                         'unlink changes references, not full API credentials')

    def test_agent_manifest_is_live_for_sidebar_edits_and_export(self):
        from easyread.library.library import Library
        pid, prefix = self.allocated()
        ids = [f'paper-{i:02}' for i in range(15)]
        for key in [*ids, 'extra-paper']:
            doc = Path(self.tmp.name) / key
            doc.mkdir(); (doc / 'paper.json').write_text(json.dumps({'meta': {'title_en': key}}))
        manifest = self.remote_root / 'workspace' / pid / '.easyread-papers.json'
        manifest.write_text(json.dumps({'papers': ids, 'memo': 'keep'}))  # native Agent, no GUI link call
        with patch.object(self.app.workspaces.sessions, 'capabilities', side_effect=AssertionError('unneeded native probe')):
            self.assertEqual(self.request('/api/workspaces')[2]['projects'][0]['id'], pid)
            self.assertFalse(self.connections)
        self.assertEqual(self.request(prefix)[2]['project']['papers'], ids)
        url = prefix + '/papers?linked_only=1'
        with patch.object(remote_worker, 'capabilities', side_effect=AssertionError('paper polling fetched project capabilities')):
            data = self.request(url)[2]
            self.assertEqual([x['title_en'] for x in data['linked']], ids)
            if os.name != 'nt':
                check = url + '&if_version=' + data['version']
                with patch.object(Library, 'summary', side_effect=AssertionError('reparsed unchanged paper')):
                    self.assertTrue(self.request(check)[2]['unchanged'])
                (Path(self.tmp.name) / ids[0] / 'item.json').write_text('{"meta_override":{"title_en":"Renamed"}}')
                self.assertEqual(self.request(check)[2]['linked'][0]['title_en'], 'Renamed')
        # Before any GUI link mutation the archive must already include the Agent's links.
        with zipfile.ZipFile(io.BytesIO(self.request(prefix + '/export')[2])) as archive:
            self.assertEqual(json.loads(archive.read('manifest.json'))['project']['papers'], ids)
        self.assertEqual(self.request(prefix + '/papers/link', {'document_id': 'extra-paper'})[0], 200)
        self.assertEqual(self.request(prefix + '/papers/unlink', {'document_id': ids[0]})[0], 200)
        self.assertEqual(json.loads(manifest.read_bytes()), {'papers': [*ids[1:], 'extra-paper'], 'memo': 'keep'})
        manifest.write_text('{"papers":[]}')
        self.assertEqual(self.request(url)[2]['linked'], [])  # explicit clear wins over cached metadata
        manifest.write_text('{"papers":')
        self.assertEqual(self.request(url)[0], 409)
        self.assertEqual(self.request(prefix + '/papers/link', {'document_id': 'extra-paper'})[0], 409)
        self.assertEqual(manifest.read_text(), '{"papers":')
        self.assertEqual(self.request(prefix)[0], 200, 'the project must still open for repair')

    def test_delete_confirmed_missing_reference_does_not_touch_local_project(self):
        pid, prefix = self.allocated()
        self.worker.app.workspaces.store.delete(pid)
        with patch.object(self.app.workspaces.store, 'delete', side_effect=AssertionError('local delete')):
            status, _, result = self.request(prefix + '/delete', {})
        self.assertEqual((status, result), (200, {'ok': True, 'missing': True}))
        self.assertIsNone(self.manager.registry.project(pid))


if __name__ == '__main__':
    unittest.main()

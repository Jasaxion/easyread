"""Real HTTP/filesystem integration; never calls an AI provider."""
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch
from urllib.parse import urlencode

from easyread.app import config
from easyread.server import launch, server, workspaces_api


class WorkspaceHTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='easyread-workspace-http-')
        self.env = patch.dict(os.environ, {'EASYREAD_LIBRARY': self.tmp.name,
            'EASYREAD_ACCESS_CODE': 'test-workspace-code', 'EASYREAD_TERMINAL': '',
            'EASYREAD_BEADS_COMMAND': 'easyread-no-bd-in-http-test'})
        self.env.start()
        self.app = server.App(config.load())
        self.handler_patch = patch.object(server.Handler, 'app', self.app, create=True)
        self.handler_patch.start()
        self.httpd = launch._Server(('127.0.0.1', 0), server.Handler)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ''
        self.token = ''

    def tearDown(self):
        workspaces_api.close(self.app)
        self.app.public_api.close()
        self.app.terminals.close()
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(5)
        self.handler_patch.stop(); self.env.stop(); self.tmp.cleanup()

    def request(self, path, body=None, *, raw=False, auth=True, headers=None, method=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        headers = {'Origin': f'http://127.0.0.1:{self.port}', 'Content-Type': 'application/json', **(headers or {})}
        if auth:
            headers.update(Cookie=self.cookie, **{'X-Token': self.token})
        data = body if raw else json.dumps(body).encode() if body is not None else None
        try:
            conn.request(method or ('POST' if body is not None else 'GET'), path, data, headers)
            response = conn.getresponse()
            result = response.read()
            if response.getheader('Content-Type', '').startswith('application/json'):
                result = json.loads(result)
            return response.status, dict(response.getheaders()), result
        finally:
            conn.close()

    def login(self):
        status, headers, _ = self.request('/api/access/login', {'code': 'test-workspace-code'})
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';')[0]
        status, _, listing = self.request('/api/workspaces')
        self.assertEqual(status, 200, listing)
        self.token = listing['token']
        self.assertTrue(listing['library_id'])
        return listing

    def create(self):
        status, _, data = self.request('/api/workspaces/create', {'name': 'HTTP Project', 'description': 'Study'})
        self.assertEqual(status, 200, data)
        return data['project']['id']

    def test_access_gate_csrf_and_html(self):
        for path in ('/api/workspaces', '/api/workspaces/invalid/export', '/workspace-terminal/invalid/'):
            self.assertEqual(self.request(path, auth=False)[0], 401)
        self.assertEqual(self.request('/workspaces', auth=False)[0], 302)
        self.login()
        status, _, page = self.request('/workspaces')
        self.assertEqual(status, 200)
        self.assertIn(b'ws-project-list', page)
        self.token = ''
        self.assertEqual(self.request('/api/workspaces/create', {'name': 'CSRF rejected'})[0], 403)

    def test_model_catalog_auth_project_validation_and_read_only_route(self):
        from easyread.workspaces import model_catalog
        self.assertEqual(self.request('/api/workspaces/invalid/models?engine=codex', auth=False)[0], 401)
        self.login()
        pid = self.app.workspaces.store.allocate({'name': 'Catalog route'})['id']
        prefix = '/api/workspaces/' + pid
        payload = {'engine': 'codex', 'default': '', 'models': [], 'source': 'configured',
                   'installed': False, 'warning': 'cli_missing'}
        with patch.object(model_catalog, 'listing', return_value=payload) as listing:
            self.assertEqual(self.request('/api/workspaces/invalid/models?engine=codex')[0], 404)
            listing.assert_not_called()
            status, _, result = self.request(prefix + '/models?engine=codex&refresh=1')
            self.assertEqual((status, result), (200, payload))
            listing.assert_called_once_with(self.app.workspaces.store, pid, 'codex', refresh=True)
            self.assertEqual(self.request(prefix + '/models?engine=codex&refresh=bad')[0], 400)
        self.assertEqual(self.request(prefix + '/models?engine=unknown')[0], 400)
        self.assertEqual(self.request(prefix + '/models')[0], 400)
        self.assertFalse((self.app.workspaces.store.meta(pid) / 'sessions').exists())

    def test_beads_routes_use_current_project_auth_and_token(self):
        from easyread.workspaces import beads
        self.assertEqual(self.request('/api/workspaces/invalid/beads', auth=False)[0], 401)
        self.login(); pid = self.create(); prefix = '/api/workspaces/' + pid + '/beads'
        root = self.app.workspaces.store.root(pid)
        payload = {'issues': [], 'ready_ids': [], 'blocked_ids': [], 'truncated': False}
        with patch.object(beads, 'listing', return_value=payload) as listing:
            self.assertEqual(self.request(prefix)[2], payload)
            listing.assert_called_once_with(root)
            self.assertEqual(self.request('/api/workspaces/invalid/beads')[0], 404)
            self.assertEqual(listing.call_count, 1)
        with patch.object(beads, 'show', return_value={'issue': {'id': 'research-abc'}, 'comments': []}) as show:
            self.assertEqual(self.request(prefix + '/show?id=research-abc')[0], 200)
            show.assert_called_once_with(root, 'research-abc')
        with patch.object(beads, 'action', return_value={'ok': True}) as action:
            self.assertEqual(self.request(prefix + '/comment', {'id': 'research-abc', 'text': 'progress'})[0], 200)
            action.assert_called_once_with(root, 'comment', {'id': 'research-abc', 'text': 'progress'})
            token = self.token; self.token = ''
            self.assertEqual(self.request(prefix + '/create', {'title': 'CSRF'})[0], 403)
            self.token = token
            self.assertEqual(self.request(prefix + '/update', {'id': 'research-abc'}, auth=False)[0], 401)
            self.assertEqual(action.call_count, 1)
        self.assertEqual(self.request(prefix)[0], 503, 'missing bd is not a successful empty board')

    def test_project_file_conflict_raw_and_archive_end_to_end(self):
        self.login(); pid = self.create(); prefix = '/api/workspaces/' + pid
        _, _, data = self.request(prefix + '/file?' + urlencode({'path': 'README.md'}))
        root = self.app.workspaces.store.root(pid)
        (root / 'README.md').write_text('External Agent version')
        status, _, error = self.request(prefix + '/files/write', {'path': 'README.md', 'content': 'local draft', 'version': data['version']})
        self.assertEqual(status, 409, error)
        self.assertEqual((root / 'README.md').read_text(), 'External Agent version')
        self.assertEqual(self.request(prefix + '/files/create', {'path': 'copy.md', 'content': 'local draft'})[0], 200)
        self.assertEqual(self.request(prefix + '/files/create', {'path': '../escape'})[0], 400)
        self.assertEqual(self.request(prefix + '/files/upload?path=page.html', b'<script>window.evil=1</script>', raw=True)[0], 200)
        status, headers, content = self.request(prefix + '/raw?path=page.html')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Disposition'].startswith('attachment'))
        status, _, data = self.request(prefix + '/export')
        self.assertEqual(status, 200, data)
        status, _, imported = self.request('/api/workspaces/import', data, raw=True)
        self.assertEqual(status, 200, imported)
        self.assertNotEqual(imported['project']['id'], pid)
        self.assertEqual((self.app.workspaces.store.root(imported['project']['id']) / 'copy.md').read_text(), 'local draft')
        self.assertEqual(self.request(prefix + '/update', {'name': 'Renamed'})[2]['project']['id'], pid)
        self.assertEqual(self.request(prefix + '/delete', {})[0], 200)

    def test_latex_initialization_is_lazy_and_settings_persist(self):
        self.login(); pid = self.create(); prefix = '/api/workspaces/' + pid
        root = self.app.workspaces.store.root(pid)
        self.assertFalse(workspaces_api.busy(self.app))
        self.assertFalse((root / 'latex-workspace').exists())
        status, _, state = self.request(prefix + '/latex')
        self.assertEqual(status, 200, state)
        self.assertTrue((root / 'latex-workspace/main.tex').is_file())
        self.assertEqual(self.request(prefix + '/latex/settings', {'main': 'main.tex', 'engine': 'xelatex'})[0], 200)
        self.assertEqual(self.request(prefix + '/latex')[2]['engine'], 'xelatex')
        self.assertEqual(self.request(prefix + '/latex/settings', {'main': '../outside.tex'})[0], 400)

    def test_raw_pdf_ranges_head_and_link_guards(self):
        self.login(); pid = self.create(); prefix = '/api/workspaces/' + pid
        root = self.app.workspaces.store.root(pid)
        content = b'%PDF-test' + b'x' * 200000
        (root / 'preview.pdf').write_bytes(content)
        for value, expected in (('bytes=5-12', content[5:13]), ('bytes=-5', content[-5:]), ('bytes=200000-', content[200000:])):
            status, headers, data = self.request(prefix + '/raw?path=preview.pdf', headers={'Range': value})
            self.assertEqual((status, data), (206, expected))
            self.assertEqual(headers['Accept-Ranges'], 'bytes')
            self.assertTrue(headers['Content-Range'].endswith('/' + str(len(content))))
        for value in ('bytes=999999-', 'bytes=5-1', 'bytes=-0', 'bytes=0-1,3-4', 'bad', 'bytes=-'):
            status, headers, data = self.request(prefix + '/raw?path=preview.pdf', headers={'Range': value})
            self.assertEqual((status, data), (416, b''))
            self.assertEqual(headers['Content-Range'], 'bytes */' + str(len(content)))
        status, headers, data = self.request(prefix + '/raw?path=preview.pdf', headers={'Range': 'bytes=0-4'}, method='HEAD')
        self.assertEqual((status, headers['Content-Length'], data), (206, '5', b''))
        try:
            (root / 'link.pdf').symlink_to(root / 'preview.pdf')
        except OSError:
            self.skipTest('Symlinks unavailable; range checks passed')
        self.assertEqual(self.request(prefix + '/raw?path=link.pdf')[0], 403)
        os.link(root / 'preview.pdf', root / 'hard.pdf')
        self.assertEqual(self.request(prefix + '/raw?path=hard.pdf')[0], 403)

    def test_latex_raw_zip_idle_replace_and_sync_version_contract(self):
        self.login(); pid = self.create(); prefix = '/api/workspaces/' + pid
        self.request(prefix + '/latex')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('wrapped/main.tex', 'imported source')
        raw = output.getvalue()
        status, _, error = self.request(prefix + '/latex/import-zip', raw, raw=True)
        self.assertEqual((status, error['code']), (409, 'file_conflict'))
        with patch.object(self.app.workspaces.sessions, 'busy', return_value=True):
            status, _, error = self.request(prefix + '/latex/import-zip?replace=1', raw, raw=True)
        self.assertEqual((status, error['code']), (409, 'project_busy'))
        status, _, result = self.request(prefix + '/latex/import-zip?replace=1', raw, raw=True)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['imported'], ['latex-workspace/main.tex'])
        self.assertEqual(result['compilation']['state'], 'idle')
        for operation, body in (('forward-sync', {'path': 'latex-workspace/main.tex', 'line': 1}),
                                ('inverse-sync', {'page': 1, 'x': 0, 'y': 0})):
            status, _, error = self.request(prefix + '/latex/' + operation, {**body, 'pdf': 'old.pdf'})
            self.assertEqual((status, error['code']), (409, 'stale_pdf'))

    def test_session_incremental_routes_auth_and_native_capability_errors(self):
        with patch('easyread.workspaces.codex_protocol.probe', return_value={
                'app_server': False, 'goal': False, 'steer': False, 'user_input': False}), \
                patch('easyread.workspaces.session_cli.engine_status', side_effect=lambda e, t: {'id': e, 'capabilities': {}}):
            self.login(); pid = self.create()
            prefix = '/api/workspaces/' + pid + '/sessions'
            status, _, created = self.request(prefix + '/create', {'engine': 'claude'})
            self.assertEqual(status, 200, created)
            sid, cursor = created['session']['id'], created['cursor']
            scope = prefix + '/' + sid
            self.assertEqual(created['session']['reasoning_effort'], '')
            status, _, settings = self.request(scope + '/settings', {'model': 'custom', 'reasoning_effort': ''})
            self.assertEqual(status, 200, settings)
            self.assertEqual(settings['session']['model'], 'custom')
            self.assertEqual(settings['session']['native_id'], created['session']['native_id'])
            self.assertEqual(self.request(scope + '/settings', {'reasoning_effort': 'minimal'})[0], 400)
            self.assertEqual(self.request(scope + '/settings', {}, auth=False)[0], 401)
            listed = self.request(prefix)[2]['sessions'][0]
            self.assertEqual(listed['adapter'], 'claude_exec')
            self.assertNotIn('messages', listed)
            self.assertNotIn('events', listed)
            self.assertEqual(self.request(scope + '/updates?after=' + str(cursor))[2]['messages'], [])
            self.assertEqual(self.request(scope + '/history?limit=40')[2]['messages'], [])
            renamed = self.request(scope + '/rename', {'title': 'Durable name'})[2]
            self.assertGreater(renamed['cursor'], cursor)
            updates = self.request(scope + '/updates?after=' + str(cursor))[2]
            self.assertEqual(updates['session']['title'], 'Durable name')
            self.assertGreater(updates['next_cursor'], cursor)
            for operation, body in [('goal', {'action': 'set', 'objective': 'not supported'}),
                                    ('steer', {'message': 'not supported'}),
                                    ('respond', {'request_id': '7', 'generation': 'expired'})]:
                status, _, error = self.request(scope + '/' + operation, body)
                self.assertEqual((status, error['code']), (409, 'capability_unavailable'))
                self.assertEqual(self.request(scope + '/' + operation, body, auth=False)[0], 401)
            self.assertEqual(self.request(scope + '/updates?after=bad')[0], 400)
            self.token = ''
            self.assertEqual(self.request(scope + '/settings', {'reasoning_effort': ''})[0], 403)
            self.assertEqual(self.request(scope + '/goal', {'action': 'clear'})[0], 403)

    def test_library_move_guard_covers_project_writes_and_reads(self):
        self.login(); pid = self.create()
        self.app.location.status = 'restart_required'
        for path in ('/api/workspaces', '/api/workspaces/' + pid + '/tree'):
            self.assertEqual(self.request(path)[0], 409)
        self.assertEqual(self.request('/api/workspaces/' + pid + '/update', {'name': 'blocked'})[0], 400)


if __name__ == '__main__':
    unittest.main()

"""Beads transport, validation and opt-in native smoke in disposable projects."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from easyread.workspaces import WorkspaceError, beads, beads_cli
from easyread.workspaces.store import Store


class BeadsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='easyread-beads-test-')
        self.root = Path(self.tmp.name)
        self.db = self.root / '.beads'
        (self.db / 'embeddeddolt/research/.dolt').mkdir(parents=True)
        (self.db / 'metadata.json').write_text(json.dumps({'database': 'dolt', 'backend': 'dolt', 'dolt_mode': 'embedded', 'dolt_database': 'research', 'project_id': 'sample'}))
        (self.db / 'config.yaml').write_text('# Defaults\n')
        (self.db / '.local_version').write_text('1.3.4')
        (self.root / '.git').mkdir()
        (self.root / '.git/config').write_text('[core]\nrepositoryformatversion = 0\nbare = false\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_rejects_redirection_server_custom_yaml_and_git(self):
        beads_cli.validate(self.root)
        for name in ('redirect', '.env', 'routes.jsonl', 'config.json'):
            with self.subTest(name=name):
                (self.db / name).write_text('elsewhere')
                with self.assertRaises(WorkspaceError):
                    beads_cli.validate(self.root)
                (self.db / name).unlink()
        path = self.db / 'metadata.json'; original = path.read_text()
        for key, value in [('dolt_mode', 'server'), ('dolt_data_dir', '/outside'), ('database', '../other'), ('backend', 'extension'), ('dolt_database', '../other')]:
            with self.subTest(key=key):
                path.write_text(json.dumps({**json.loads(original), key: value}))
                with self.assertRaises(WorkspaceError):
                    beads_cli.validate(self.root)
        path.write_text(original)
        for name in ('config.yaml', 'config.local.yaml'):
            (self.db / name).write_text('routing:\n  default: /outside\n')
            with self.assertRaises(WorkspaceError):
                beads_cli.validate(self.root)
            (self.db / name).write_text('# safe\n')
        (self.root / '.git/config').write_text('[include]\npath = /outside\n')
        with self.assertRaises(WorkspaceError):
            beads_cli.validate(self.root)

    def test_links_are_rejected_before_starting_bd(self):
        target = self.root / 'outside'; target.write_text('outside')
        try:
            (self.db / 'link').symlink_to(target)
        except OSError:
            self.skipTest('symlinks unavailable')
        with self.assertRaises(WorkspaceError):
            beads_cli.validate(self.root)
        (self.db / 'link').unlink(); os.link(target, self.db / 'hardlink')
        with self.assertRaises(WorkspaceError):
            beads_cli.validate(self.root)

    def test_environment_has_no_host_selectors_credentials_or_user_config(self):
        with patch.dict(os.environ, {'BEADS_DIR': '/unrelated', 'BD_DB': '/other', 'BEADS_DOLT_SERVER_HOST': 'remote',
                                      'DOLT_CLI_PASSWORD': 'secret', 'GIT_CONFIG_COUNT': '50', 'LD_PRELOAD': '/bad', 'OPENAI_API_KEY': 'secret'}):
            env = beads_cli.environment(self.root, self.root / 'isolated-home')
        self.assertEqual(env['BEADS_DIR'], str(self.db))
        self.assertEqual(env['BD_DB'], str(self.db / 'dolt'))
        self.assertNotIn('BEADS_DOLT_SERVER_HOST', env); self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('DOLT_CLI_PASSWORD', env); self.assertNotIn('LD_PRELOAD', env)
        self.assertEqual(env['BD_ROUTING_DEFAULT'], '.'); self.assertEqual(env['BD_NO_HOOKS'], 'true')
        self.assertEqual(env['BD_IMPORT_AUTO'], 'false'); self.assertNotEqual(env['HOME'], os.environ.get('HOME'))

    def test_strict_readonly_and_version_guard(self):
        calls = []
        def execute(command, root, env):
            calls.append(command)
            return 'bd version 1.3.4 (official)' if '--version' in command else '[]'
        with patch.object(beads_cli.shutil, 'which', return_value='/trusted/bd'), patch.object(beads_cli, 'execute', side_effect=execute):
            client = beads_cli.Client(self.root)
            self.assertEqual(client.run(['list']), [])
            self.assertIn('--readonly', calls[-1]); self.assertIn('--sandbox', calls[-1])
            self.assertIn('--db=' + str(self.db / 'dolt'), calls[-1])
            (self.db / '.local_version').write_text('old')
            with self.assertRaises(WorkspaceError) as error:
                client.run(['create', '--title=test'], write=True)
            self.assertEqual(error.exception.code, 'beads_version')
            self.assertFalse(any('create' in cmd for cmd in calls))

    def test_fields_are_validated_before_any_cli_or_lookup(self):
        with patch.object(beads, 'Client') as client:
            for operation, body in [('create', {'title': 'x', 'priority': True}), ('update', {'id': 'x', 'status': 'oops', 'expected_revision': 'r'}),
                                    ('comment', {'id': '../other', 'text': 'x'}), ('create', {'title': 'x', 'labels': ['a,b']}),
                                    ('dependency', {'id': 'x', 'depends_on': 'x'}), ('update', {'id': 'x', 'title': 'x'})]:
                with self.subTest(body=body), self.assertRaises(WorkspaceError):
                    beads.action(self.root, operation, body)
            client.assert_not_called()

    def test_stale_revision_and_exact_local_ids(self):
        class Fake:
            def __init__(self, root): pass
            def run(self, args, *, write=False):
                self_test.assertFalse(write, 'invalid request cannot write')
                return [{'id': 'research-abc', 'status': 'open', 'revision': 'new'}]
        self_test = self
        with patch.object(beads, 'Client', Fake):
            with self.assertRaises(WorkspaceError) as error:
                beads.action(self.root, 'update', {'id': 'research-abc', 'title': 'edit', 'expected_revision': 'old'})
            self.assertEqual(error.exception.code, 'beads_conflict')
            with self.assertRaises(WorkspaceError) as error:
                beads.show(self.root, 'abc')
            self.assertEqual(error.exception.status, 404)

    def test_output_and_timeout_are_bounded(self):
        with patch.object(beads_cli, 'MAX_OUTPUT', 1024):
            with self.assertRaises(WorkspaceError) as error:
                beads_cli.execute([sys.executable, '-c', 'print("x"*100000)'], self.root, os.environ.copy())
            self.assertEqual(error.exception.code, 'beads_output_limit')
        with patch.object(beads_cli, 'TIMEOUT', 0.05):
            with self.assertRaises(WorkspaceError) as error:
                beads_cli.execute([sys.executable, '-c', 'import time;time.sleep(10)'], self.root, os.environ.copy())
            self.assertEqual(error.exception.code, 'beads_timeout')


@unittest.skipUnless(os.environ.get('EASYREAD_BEADS_SMOKE_COMMAND'), 'set EASYREAD_BEADS_SMOKE_COMMAND to an official bd binary')
class NativeBeadsTest(unittest.TestCase):
    def test_two_projects_crud_dependencies_comments_and_isolation(self):
        exe = os.environ['EASYREAD_BEADS_SMOKE_COMMAND']
        clean = {key: value for key, value in os.environ.items() if not key.startswith(('BD_', 'BEADS_', 'DOLT_', 'GIT_'))}
        clean['EASYREAD_BEADS_COMMAND'] = exe
        with tempfile.TemporaryDirectory(prefix='easyread-bd-native-') as temp, patch.dict(os.environ, clean, clear=True):
            home = Path(temp) / 'home'; home.mkdir()
            for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME'):
                os.environ[key] = str(home)
            store = Store(Path(temp) / 'library')
            a = store.create({'name': 'A'})['project']; b = store.create({'name': 'B'})['project']
            self.assertTrue(a['initialization']['beads']['ok'], a)
            self.assertTrue(b['initialization']['beads']['ok'], b)
            root, other = store.root(a['id']), store.root(b['id'])
            def create(where, title):
                return beads.action(where, 'create', {'title': title, 'description': 'Native smoke', 'priority': 1, 'labels': ['test']})['issue']['id']
            first, second, foreign = create(root, 'First'), create(root, 'Second'), create(other, 'Private B')
            revision = beads.show(root, first)['issue']['revision']
            beads.action(root, 'update', {'id': first, 'expected_revision': revision, 'title': '--literal <script>safe</script>', 'labels': [], 'assignee': 'Reviewer'})
            with self.assertRaises(WorkspaceError) as stale:
                beads.action(root, 'update', {'id': first, 'expected_revision': revision, 'title': 'stale'})
            self.assertEqual(stale.exception.code, 'beads_conflict')
            beads.action(root, 'comment', {'id': first, 'text': '--literal progress comment'})
            self.assertEqual(beads.show(root, first)['comments'][-1]['text'], '--literal progress comment')
            beads.action(root, 'dependency', {'id': second, 'depends_on': first})
            self.assertIn(second, beads.listing(root)['blocked_ids'])
            def status(id, value):
                return beads.action(root, 'update', {'id': id, 'expected_revision': beads.show(root, id)['issue']['revision'], 'status': value})
            with self.assertRaises(WorkspaceError):
                status(second, 'closed')
            self.assertEqual(beads.show(root, second)['issue']['status'], 'open')
            with self.assertRaises(WorkspaceError):
                status(first, 'closed')  # Never override another actor's assignment with --force.
            beads.action(root, 'update', {'id': first, 'expected_revision': beads.show(root, first)['issue']['revision'], 'assignee': ''})
            status(first, 'closed'); self.assertIn(second, beads.listing(root)['ready_ids'])
            status(first, 'open'); status(first, 'in_progress')
            beads.action(root, 'dependency', {'id': second, 'depends_on': first, 'remove': True})
            self.assertNotIn(second, beads.listing(root)['blocked_ids'])
            for op, body in [('comment', {'id': foreign, 'text': 'must not write'}), ('dependency', {'id': first, 'depends_on': foreign})]:
                with self.assertRaises(WorkspaceError) as error:
                    beads.action(root, op, body)
                self.assertEqual(error.exception.status, 404)
            # The UI never executes project hooks. No command touches the marker.
            hooks = root / '.beads/hooks'; hooks.mkdir()
            marker = Path(temp) / 'HOOK-RAN'
            hook = hooks / 'on_create'; hook.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n'); hook.chmod(0o700)
            create(root, 'Hooks disabled'); self.assertFalse(marker.exists())
            with patch.dict(os.environ, {'BEADS_DIR': str(other / '.beads'), 'BD_DB': str(other / '.beads/dolt'), 'BEADS_DOLT_SERVER_HOST': 'remote.invalid'}):
                self.assertNotIn(foreign, {item['id'] for item in beads.listing(root)['issues']})
            (root / '.beads/redirect').write_text(str(other / '.beads'))
            with self.assertRaises(WorkspaceError):
                beads.listing(root)
            (root / '.beads/redirect').unlink()
            self.assertEqual(beads.show(other, foreign)['comments'], [])
            self.assertEqual(len(beads.listing(other)['issues']), 1)
            # Independent native CLI readback, without the adapter's Client.
            env = {**os.environ, 'BEADS_DIR': str(root / '.beads')}
            result = subprocess.run([exe, '--json', 'show', first], cwd=root, env=env, capture_output=True, text=True, timeout=30, check=True)
            saved = json.loads(result.stdout)[0]
            self.assertEqual(saved['title'], '--literal <script>safe</script>')
            self.assertEqual(saved['status'], 'in_progress'); self.assertFalse(saved.get('labels'))

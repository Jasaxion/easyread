"""Focused project/files/archive regression; real Git, no model or user data."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from easyread.workspaces import WorkspaceError, archives, files, git
from easyread.workspaces.store import Store


class State:
    def export_state(self, pid, dest):
        (dest / 'transcript.json').write_text('{"messages":["test"]}')

    def import_state(self, pid, source):
        assert (source / 'transcript.json').is_file()


class WorkspaceStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='easyread-workspace-test-')
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'library')
        with patch.dict(os.environ, {'EASYREAD_BEADS_COMMAND': 'easyread-nonexistent-bd'}):
            self.project = self.store.create({'name': 'First project', 'description': 'A searchable study'})['project']
        self.pid = self.project['id']
        self.root = self.store.root(self.pid)

    def test_create_rename_search_delete(self):
        self.assertRegex(self.pid, r'^[a-z]{20}$')
        for name in ('README.md', 'AGENTS.md', 'CLAUDE.md', '.gitignore'):
            self.assertTrue((self.root / name).is_file())
        self.assertTrue((self.root / '.git').is_dir())
        self.assertFalse(self.project['initialization']['beads']['ok'])
        self.store.update(self.pid, {'name': 'Renamed'})
        self.assertEqual(self.store.root(self.pid), self.root)
        self.assertEqual(self.store.listing('searchable')[0]['name'], 'Renamed')
        self.assertEqual(self.store.listing('missing'), [])
        self.store.delete(self.pid)
        self.assertEqual(self.store.listing(), [])

    def test_files_conflict_hidden_binary_and_move(self):
        files.create(self.root, '.hidden', 'directory')
        files.create(self.root, '.hidden/a.md', content='first')
        current = files.read(self.root, '.hidden/a.md')
        (self.root / '.hidden/a.md').write_text('Agent edit')
        with self.assertRaises(WorkspaceError) as err:
            files.write(self.root, '.hidden/a.md', 'browser draft', current['version'])
        self.assertEqual(err.exception.status, 409)
        self.assertEqual((self.root / '.hidden/a.md').read_text(), 'Agent edit')
        files.create(self.root, '.hidden/draft.md', content='browser draft')
        files.move(self.root, '.hidden/draft.md', 'saved.md')
        files.upload(self.root, 'binary.dat', b'\x00\xffbinary')
        self.assertTrue(files.read(self.root, 'binary.dat')['binary'])
        self.assertIn('.hidden/a.md', [x['path'] for x in files.tree(self.root)['entries']])
        with self.assertRaises(WorkspaceError):
            files.upload(self.root, 'saved.md', b'overwrite')
        files.delete(self.root, '.hidden')
        self.assertFalse((self.root / '.hidden').exists())

    def test_path_and_link_boundary(self):
        for name in ('../escape', '/tmp/escape', 'x/../../escape', 'x\\escape', 'x//escape', 'a:b', ''):
            with self.subTest(name=name), self.assertRaises(WorkspaceError):
                files.create(self.root, name)
        outside = Path(self.tmp.name) / 'outside'
        outside.write_text('outside')
        (self.root / 'link').symlink_to(outside)
        with self.assertRaises(WorkspaceError):
            files.read(self.root, 'link')
        files.delete(self.root, 'link')
        self.assertEqual(outside.read_text(), 'outside')
        os.link(outside, self.root / 'hardlink')
        with self.assertRaises(WorkspaceError):
            files.read(self.root, 'hardlink')

    def test_git_ignores_hooks_filters_and_custom_includes(self):
        marker = self.root / 'should-not-run'
        hook = self.root / '.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n')
        hook.chmod(0o755)
        with (self.root / '.git/config').open('a') as stream:
            stream.write('\n[filter "evil"]\nclean = touch ' + str(marker) + '\nrequired = true\n')
        include = self.root / '.evil-git-config'
        include.write_text('[core]\nfsmonitor = touch ' + str(marker) + '\n[filter "evil"]\nclean = touch ' + str(marker) + '\nrequired = true\n')
        with (self.root / '.git/config').open('a') as stream:
            stream.write('[include]\npath = ' + str(include) + '\n[core]\nworktree = ' + self.tmp.name + '\n')
        (self.root / '.gitattributes').write_text('* filter=evil\n')
        original = (self.root / '.git/config').read_bytes()
        result = git.action(self.root, {'action': 'commit', 'message': 'Isolated test'})
        self.assertIn('Isolated test', result['output'])
        self.assertFalse(marker.exists())
        self.assertEqual((self.root / '.git/config').read_bytes(), original)
        self.assertEqual(git.status(self.root)['status'], '')

    def test_archive_round_trip_preserves_files_modes_empty_dirs_and_history(self):
        files.create(self.root, 'empty', 'directory')
        (self.root / '.beads').mkdir()
        (self.root / '.beads/tasks.db').write_bytes(b'\x00\xffbeads native data')
        (self.root / '.hidden').write_text('complete hidden file')
        files.upload(self.root, 'raw.bin', b'\x00\xff\x01')
        script = self.root / 'example.sh'
        script.write_text('#!/bin/sh\necho test\n')
        script.chmod(0o755)
        git.action(self.root, {'action': 'commit', 'message': 'Archive test'})
        raw = archives.export_project(self.store, State(), self.pid)
        restored = archives.import_project(self.store, State(), raw)['project']
        root = self.store.root(restored['id'])
        self.assertNotEqual(restored['id'], self.pid)
        self.assertTrue((root / 'empty').is_dir())
        self.assertEqual((root / 'raw.bin').read_bytes(), b'\x00\xff\x01')
        self.assertEqual((root / '.beads/tasks.db').read_bytes(), b'\x00\xffbeads native data')
        self.assertEqual((root / '.hidden').read_text(), 'complete hidden file')
        self.assertEqual(stat.S_IMODE((root / 'example.sh').stat().st_mode), 0o755)
        self.assertEqual(git.run(root, ['log', '-1', '--format=%s']), 'Archive test')
        self.assertFalse((root / 'latex-workspace').exists())

    def test_archive_rejects_unsafe_duplicate_symlink_and_bomb_paths(self):
        for names in (['../escape'], ['workspace/x', 'workspace/X'], ['/escape']):
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, 'w') as z:
                for name in names:
                    z.writestr(name, 'content')
            with self.subTest(names=names), self.assertRaises(WorkspaceError):
                archives.import_project(self.store, State(), raw.getvalue())
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as z:
            info = zipfile.ZipInfo('workspace/link')
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, '../../secret')
        with self.assertRaises(WorkspaceError):
            archives.import_project(self.store, State(), raw.getvalue())
        self.assertEqual(len(self.store.listing()), 1)

    def test_files_read_hashes_exact_returned_snapshot(self):
        path = self.root / 'README.md'
        original = path.read_bytes()
        read_bytes = files.read_bytes

        def replace_after_read(file, *args):
            raw = read_bytes(file, *args)
            file.write_bytes(b'Agent replaced the disk content')
            return raw

        with patch.object(files, 'read_bytes', side_effect=replace_after_read):
            result = files.read(self.root, 'README.md')
        self.assertEqual(result['content'].encode(), original)
        self.assertEqual(result['version'], hashlib.sha256(result['content'].encode()).hexdigest())
        self.assertEqual(result['size'], len(original))
        self.assertNotEqual(path.read_bytes(), original)

    def test_files_read_rejects_link_swapped_at_open(self):
        path = self.root / 'README.md'
        outside = Path(self.tmp.name) / 'secret'
        outside.write_text('outside private bytes')
        original_open = os.open

        def replace_at_open(name, flags, *args, **kwargs):
            if Path(name).name == path.name and not flags & os.O_CREAT:
                path.unlink()
                path.symlink_to(outside)
            return original_open(name, flags, *args, **kwargs)

        with patch.object(os, 'open', side_effect=replace_at_open):
            with self.assertRaises(WorkspaceError):
                files.read(self.root, path.name)
        self.assertEqual(outside.read_text(), 'outside private bytes')

    def test_files_read_rejects_parent_swapped_after_path_check(self):
        folder = self.root / 'nested'
        folder.mkdir()
        (folder / 'note').write_text('inside')
        outside = Path(self.tmp.name) / 'outside-folder'
        outside.mkdir()
        (outside / 'note').write_text('outside')
        original_read = files.read_bytes

        def swap_parent(path, *args):
            folder.rename(self.root / 'old-nested')
            folder.symlink_to(outside, target_is_directory=True)
            return original_read(path, *args)

        with patch.object(files, 'read_bytes', side_effect=swap_parent):
            with self.assertRaises(WorkspaceError):
                files.read(self.root, 'nested/note')

    def test_files_write_preserves_race_between_check_and_displacement(self):
        path = self.root / 'README.md'
        current = files.read(self.root, path.name)
        relocate = files.relocate

        def change_before_move(source, dest):
            if source == path:
                source.write_text('Agent revision at rename')
            relocate(source, dest)

        with patch.object(files, 'relocate', side_effect=change_before_move):
            with self.assertRaises(WorkspaceError) as error:
                files.write(self.root, path.name, 'browser draft', current['version'])
        self.assertEqual(error.exception.status, 409)
        self.assertIn('/draft', str(error.exception))
        self.assertEqual(path.read_text(), 'Agent revision at rename')
        recovery = next((self.root / files.REVISION_DIR).iterdir())
        self.assertEqual((recovery / 'disk').read_text(), 'Agent revision at rename')
        self.assertEqual((recovery / 'draft').read_text(), 'browser draft')
        files.create(self.root, 'saved-copy.md', content=(recovery / 'draft').read_text())
        self.assertEqual((self.root / 'saved-copy.md').read_text(), 'browser draft')

    def test_files_write_never_clobbers_concurrent_creation_and_reader_sees_gap(self):
        path = self.root / 'README.md'
        current = files.read(self.root, path.name)
        publish = files.publish
        seen_gap = []

        def create_in_gap(file, content, mode=0o644):
            if file == path:
                with self.assertRaises(WorkspaceError) as missing:
                    files.read(self.root, path.name)
                seen_gap.append(missing.exception.status)
                file.write_text('Agent created new path')
            publish(file, content, mode)

        with patch.object(files, 'publish', side_effect=create_in_gap):
            with self.assertRaises(WorkspaceError) as error:
                files.write(self.root, path.name, 'browser draft', current['version'])
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(seen_gap, [404])
        self.assertEqual(path.read_text(), 'Agent created new path')
        recovery = next((self.root / files.REVISION_DIR).iterdir())
        self.assertEqual((recovery / 'disk').read_text(), current['content'])
        self.assertEqual((recovery / 'draft').read_text(), 'browser draft')

    def test_files_write_retains_late_open_descriptor_edits_and_modes(self):
        path = self.root / 'README.md'
        path.chmod(0o751)
        current = files.read(self.root, path.name)
        with path.open('r+b') as agent:
            result = files.write(self.root, path.name, 'browser draft', current['version'])
            agent.seek(0)
            agent.write(b'late Agent write after successful save')
            agent.truncate()
        recovery = next((self.root / files.REVISION_DIR).iterdir())
        self.assertEqual((recovery / 'disk').read_bytes(), b'late Agent write after successful save')
        self.assertEqual(path.read_text(), 'browser draft')
        self.assertEqual(result['version'], hashlib.sha256(b'browser draft').hexdigest())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o751)
        self.assertEqual(stat.S_IMODE((recovery / 'disk').stat().st_mode), 0o751)
        self.assertEqual(path.stat().st_nlink, 1)

    def test_files_write_reports_descriptor_edit_during_publication(self):
        path = self.root / 'README.md'
        current = files.read(self.root, path.name)
        publish = files.publish
        with path.open('r+b') as agent:
            def modify_displaced(file, content, mode=0o644):
                publish(file, content, mode)
                if file == path:
                    agent.seek(0)
                    agent.write(b'Agent edited displaced inode')
                    agent.truncate()
                    agent.flush()
            with patch.object(files, 'publish', side_effect=modify_displaced):
                with self.assertRaises(WorkspaceError) as error:
                    files.write(self.root, path.name, 'browser draft', current['version'])
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(path.read_text(), 'browser draft')
        self.assertEqual(next((self.root / files.REVISION_DIR).glob('*/disk')).read_text(), 'Agent edited displaced inode')

    def test_files_recovery_budget_and_cleanup_without_recursive_backups(self):
        path = self.root / 'README.md'
        current = files.read(self.root, path.name)
        for limits in ({'MAX_REVISION_ENTRIES': 2}, {'MAX_REVISION_BYTES': 1}):
            with patch.multiple(files, **limits):
                with self.assertRaises(WorkspaceError) as error:
                    files.write(self.root, path.name, 'draft', current['version'])
            self.assertEqual(error.exception.status, 409)
            self.assertEqual(path.read_text(), current['content'])
        files.write(self.root, path.name, 'draft', current['version'])
        disk = next((self.root / files.REVISION_DIR).glob('*/disk'))
        recovery_path = disk.relative_to(self.root).as_posix()
        with self.assertRaises(WorkspaceError):
            files.write(self.root, recovery_path, 'no recursive revision', files.version(disk))
        files.delete(self.root, files.REVISION_DIR)
        self.assertFalse((self.root / files.REVISION_DIR).exists())
        self.assertEqual(path.read_text(), 'draft')

    def test_git_rejects_indirections_before_subprocess_or_config_mutation(self):
        outside = Path(self.tmp.name) / 'outside.git'
        outside.mkdir()
        external_config = outside / 'config'
        external_config.write_text('[core]\nrepositoryformatversion = 0\n[filter "evil"]\nclean = touch marker\n')
        original = (self.root / '.git/config').read_bytes()
        for name in ('commondir', 'gitdir', 'objects/info/alternates', 'objects/info/http-alternates'):
            path = self.root / '.git' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(outside))
            try:
                with patch.object(git.subprocess, 'run') as run:
                    for action in ('status', 'commit'):
                        with self.subTest(name=name, action=action), self.assertRaises(WorkspaceError):
                            git.action(self.root, {'action': action, 'message': 'Must not execute'})
                    run.assert_not_called()
                self.assertEqual((self.root / '.git/config').read_bytes(), original)
                self.assertIn('filter "evil"', external_config.read_text())
            finally:
                path.unlink()

    def test_git_rejects_metadata_links_and_gitdir_file(self):
        outside = Path(self.tmp.name) / 'outside-config'
        outside.write_text('outside unchanged')
        for name, hardlink in (('refs/heads/evil', False), ('objects/info/evil', True)):
            path = self.root / '.git' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if hardlink:
                os.link(outside, path)
            else:
                path.symlink_to(outside)
            with self.subTest(name=name), self.assertRaises(WorkspaceError):
                git.status(self.root)
            path.unlink()
        (self.root / '.git').rename(self.root / 'real-git')
        (self.root / '.git').write_text('gitdir: ' + str(self.root / 'real-git'))
        with self.assertRaises(WorkspaceError):
            git.status(self.root)
        self.assertEqual(outside.read_text(), 'outside unchanged')

    def test_git_environment_is_explicit_and_config_inode_is_restored(self):
        cfg = self.root / '.git/config'
        cfg.chmod(0o640)
        before = cfg.stat()
        run = subprocess.run
        environments = []

        def capture(*args, **kwargs):
            environments.append(kwargs['env'])
            return run(*args, **kwargs)

        with patch.dict(os.environ, {'GIT_DIR': '/outside', 'GIT_COMMON_DIR': '/outside', 'GIT_WORK_TREE': '/outside', 'GIT_CONFIG_COUNT': '999'}):
            with patch.object(git.subprocess, 'run', side_effect=capture):
                git.status(self.root)
        for env in environments:
            self.assertEqual(env['GIT_DIR'], str(self.root / '.git'))
            self.assertEqual(env['GIT_COMMON_DIR'], str(self.root / '.git'))
            self.assertEqual(env['GIT_WORK_TREE'], str(self.root))
            self.assertNotIn('GIT_CONFIG_COUNT', env)
        self.assertEqual(cfg.stat().st_ino, before.st_ino)
        self.assertEqual(cfg.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o640)
        self.assertEqual(list((self.root / '.git' / files.REVISION_DIR).iterdir()), [])

    def test_git_config_change_during_run_is_preserved_and_conflict_visible(self):
        cfg = self.root / '.git/config'
        original = cfg.read_bytes()
        with self.assertRaises(WorkspaceError) as error:
            with git.sanitized(self.root):
                replacement = self.root / '.git/agent-config'
                replacement.write_text('[user]\nname = external change\n')
                replacement.replace(cfg)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(cfg.read_bytes(), original)
        backups = list((self.root / '.git' / files.REVISION_DIR).glob('*/temporary-config'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), '[user]\nname = external change\n')
        self.assertIn(files.REVISION_DIR, str(error.exception))

    def test_git_config_restore_never_overwrites_racing_creation(self):
        cfg = self.root / '.git/config'
        original = cfg.read_bytes()
        restore = git.restore_config

        def concurrent_config(source, destination):
            destination.write_text('[user]\nname = racing restore\n')
            restore(source, destination)

        with patch.object(git, 'restore_config', side_effect=concurrent_config):
            with self.assertRaises(WorkspaceError) as error:
                with git.sanitized(self.root):
                    pass
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(cfg.read_text(), '[user]\nname = racing restore\n')
        backup = next((self.root / '.git' / files.REVISION_DIR).glob('*/original-config'))
        self.assertEqual(backup.read_bytes(), original)

    def test_git_command_failure_still_restores_config_without_retained_noise(self):
        cfg = self.root / '.git/config'
        original = cfg.read_bytes()
        with self.assertRaises(WorkspaceError):
            git.run(self.root, ['not-a-git-command'])
        self.assertEqual(cfg.read_bytes(), original)
        self.assertEqual(list((self.root / '.git' / files.REVISION_DIR).iterdir()), [])

    def test_archives_reject_export_hardlink_and_count_empty_directories(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.write_text('must not export')
        os.link(outside, self.root / 'hardlink')
        with self.assertRaises(WorkspaceError):
            archives.export_project(self.store, State(), self.pid)
        (self.root / 'hardlink').unlink()
        source = Path(self.tmp.name) / 'dirs'
        source.mkdir()
        for name in ('a', 'b', 'c'):
            (source / name).mkdir()
        with patch.object(archives, 'MAX_ENTRIES', 3):
            with zipfile.ZipFile(io.BytesIO(), 'w') as archive:
                with self.assertRaises(WorkspaceError) as error:
                    archives._add(archive, source, 'workspace', [0, 0])
        self.assertEqual(error.exception.status, 413)

    def test_archives_actual_read_budget_and_nofollow_swap(self):
        source = Path(self.tmp.name) / 'export-source'
        source.mkdir()
        file = source / 'payload'
        file.write_text('small')
        original_open = archives.open_regular

        @contextmanager
        def grow_before_read(path):
            path.write_bytes(b'x' * 100)
            with original_open(path) as stream:
                yield stream

        with patch.object(archives, 'MAX_EXPANDED', 20), patch.object(archives, 'open_regular', grow_before_read):
            with zipfile.ZipFile(io.BytesIO(), 'w') as archive:
                with self.assertRaises(WorkspaceError) as error:
                    archives._add(archive, source, 'workspace', [0, 0])
        self.assertEqual(error.exception.status, 413)
        outside = Path(self.tmp.name) / 'outside-payload'
        outside.write_text('secret')

        @contextmanager
        def link_before_read(path):
            path.unlink()
            path.symlink_to(outside)
            with original_open(path) as stream:
                yield stream

        with patch.object(archives, 'open_regular', link_before_read):
            with zipfile.ZipFile(io.BytesIO(), 'w') as archive:
                with self.assertRaises(WorkspaceError):
                    archives._add(archive, source, 'workspace', [0, 0])

    def test_archives_reject_imported_git_indirection_without_allocation(self):
        ordinary = archives.export_project(self.store, State(), self.pid)
        for name in ('commondir', 'gitdir', 'objects/info/alternates', 'objects/info/http-alternates'):
            output = io.BytesIO(ordinary)
            with zipfile.ZipFile(output, 'a') as archive:
                archive.writestr('workspace/.git/' + name, '/outside/metadata')
            with self.subTest(name=name), self.assertRaises(WorkspaceError):
                archives.import_project(self.store, State(), output.getvalue())
            self.assertEqual(len(self.store.listing()), 1)

    def test_archives_implicit_directories_count_towards_import_budget(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, 'w') as archive:
            archive.writestr('workspace/a/b/c/file', 'small')
        with patch.object(archives, 'MAX_ENTRIES', 3):
            with self.assertRaises(WorkspaceError) as error:
                archives.import_project(self.store, State(), raw.getvalue())
        self.assertEqual(error.exception.status, 413)

    def test_archives_preserve_recovery_revisions_and_empty_roots(self):
        current = files.read(self.root, 'README.md')
        files.write(self.root, 'README.md', 'new content', current['version'])
        original_disk = next((self.root / files.REVISION_DIR).glob('*/disk'))
        raw = archives.export_project(self.store, State(), self.pid)
        imported = archives.import_project(self.store, State(), raw)['project']
        restored = self.store.root(imported['id']) / original_disk.relative_to(self.root)
        self.assertEqual(restored.read_text(), current['content'])
        project = self.store.allocate({'name': 'Empty'})
        raw = archives.export_project(self.store, State(), project['id'])
        imported = archives.import_project(self.store, State(), raw)['project']
        self.assertEqual(list(self.store.root(imported['id']).iterdir()), [])

    def test_files_noop_save_does_not_consume_recovery_budget(self):
        current = files.read(self.root, 'README.md')
        with patch.object(files, 'MAX_REVISION_ENTRIES', 0):
            result = files.write(self.root, 'README.md', current['content'], current['version'])
        self.assertEqual(result['version'], current['version'])
        self.assertFalse((self.root / files.REVISION_DIR).exists())

    def test_files_write_link_swap_never_reads_or_mutates_outside(self):
        path = self.root / 'README.md'
        outside = Path(self.tmp.name) / 'external-note'
        outside.write_text('outside unchanged')
        current = files.read(self.root, path.name)
        relocate = files.relocate

        def link_before_move(source, dest):
            if source == path:
                source.unlink()
                source.symlink_to(outside)
            relocate(source, dest)

        with patch.object(files, 'relocate', side_effect=link_before_move):
            with self.assertRaises(WorkspaceError) as error:
                files.write(self.root, path.name, 'draft', current['version'])
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(outside.read_text(), 'outside unchanged')
        self.assertEqual(next((self.root / files.REVISION_DIR).glob('*/draft')).read_text(), 'draft')

    def test_git_recovery_budget_fails_before_config_displacement(self):
        cfg = self.root / '.git/config'
        original = cfg.read_bytes()
        with patch.object(files, 'MAX_REVISION_ENTRIES', 2):
            with self.assertRaises(WorkspaceError) as error:
                git.status(self.root)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(cfg.read_bytes(), original)

    def test_archives_roundtrip_highly_compressible_normal_binary(self):
        content = b'\x00' * (2 * 1024 * 1024)
        (self.root / 'zeroes.bin').write_bytes(content)
        raw = archives.export_project(self.store, State(), self.pid)
        imported = archives.import_project(self.store, State(), raw)['project']
        self.assertEqual((self.store.root(imported['id']) / 'zeroes.bin').read_bytes(), content)

    def test_archives_imported_hooks_and_filters_do_not_execute_on_git_actions(self):
        marker = Path(self.tmp.name) / 'import-executed'
        (self.root / '.git/hooks/pre-commit').write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
        (self.root / '.git/hooks/pre-commit').chmod(0o755)
        with (self.root / '.git/config').open('a') as config:
            config.write('[filter "hostile"]\nclean = touch ' + str(marker) + '\nrequired = true\n[core]\nfsmonitor = touch ' + str(marker) + '\n')
        (self.root / '.gitattributes').write_text('* filter=hostile\n')
        raw = archives.export_project(self.store, State(), self.pid)
        imported = archives.import_project(self.store, State(), raw)['project']
        root = self.store.root(imported['id'])
        git.status(root)
        git.action(root, {'action': 'commit', 'message': 'Safe imported data'})
        self.assertFalse(marker.exists())

    def test_references_are_not_copied(self):
        doc = self.store.libroot / 'paper-test'
        doc.mkdir()
        (doc / 'paper.json').write_text(json.dumps({'schema': 2, 'meta': {'title_en': 'Test document'}, 'blocks': []}))
        self.store.link(self.pid, 'paper-test')
        linked = self.store.papers(self.pid)['linked']
        self.assertEqual(linked[0]['id'], 'paper-test')
        self.assertFalse((self.root / 'paper-test').exists())
        self.store.link(self.pid, 'paper-test', remove=True)
        self.assertTrue(doc.exists())
        self.assertEqual(self.store.papers(self.pid)['linked'], [])


class WorkspaceProcessLockTest(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Pipe readiness check requires POSIX select')
    def test_mcp_process_cannot_lose_concurrent_browser_metadata(self):
        import select
        import sys
        with tempfile.TemporaryDirectory(prefix='easyread-lock-test-') as tmp:
            store = Store(tmp)
            project = store.allocate({'name': 'Before', 'description': 'Before'})
            pid = project['id']
            source = ('import sys; from easyread.workspaces.store import Store; '
                      's=Store(sys.argv[1]); print("ready",flush=True); '
                      's.update(sys.argv[2],{"name":"MCP update"}); print("done",flush=True)')
            with store.lock(pid):
                proc = subprocess.Popen([sys.executable, '-c', source, tmp, pid], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(proc.stdout.readline().strip(), 'ready')
                    self.assertEqual(select.select([proc.stdout], [], [], .15)[0], [])
                    store.update(pid, {'description': 'Browser update'})
                except Exception:
                    proc.kill(); proc.communicate(); raise
            stdout, stderr = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertIn('done', stdout)
            result = store.get(pid)
            self.assertEqual((result['name'], result['description']), ('MCP update', 'Browser update'))


if __name__ == '__main__':
    unittest.main()

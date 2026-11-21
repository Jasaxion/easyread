"""Conditional workspace snapshots use temporary files, never a user's library."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from easyread.workspaces import WorkspaceError, files, latex


class ConditionalFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='easyread-conditional-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    @unittest.skipUnless(files._STAT_HINTS_SUPPORTED, 'Stat-only hints require POSIX change-time semantics')
    def test_file_unchanged_skips_bytes_for_text_and_binary_without_changing_sha(self):
        for name, raw in [('source.tex', b'original'), ('large.pdf', b'%PDF-\0' + b'x' * 1000000)]:
            with self.subTest(name=name):
                (self.root / name).write_bytes(raw)
                full = files.read(self.root, name)
                self.assertEqual(full['version'], hashlib.sha256(raw).hexdigest())
                self.assertNotEqual(full['stamp'], full['version'])
                open_regular = files.open_regular

                @contextmanager
                def no_bytes(path):
                    with open_regular(path) as stream:
                        with patch.object(stream, 'read', side_effect=AssertionError('read unchanged bytes')):
                            yield stream

                with patch.object(files, 'open_regular', side_effect=no_bytes):
                    self.assertEqual(files.read(self.root, name, if_stamp=full['stamp']),
                                     {'path': name, 'stamp': full['stamp'], 'unchanged': True})
                self.assertEqual(files.read(self.root, name, if_stamp='old'), full)
                self.assertEqual(files.read(self.root, name), full)

    @unittest.skipUnless(files._STAT_HINTS_SUPPORTED, 'Stat-only hints require POSIX change-time semantics')
    def test_file_rewrite_restored_mtime_replacement_and_write_conflicts(self):
        path = self.root / 'note.txt'
        path.write_bytes(b'first')
        original = files.read(self.root, path.name)
        before = path.stat()
        path.write_bytes(b'other')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        rewritten = files.read(self.root, path.name, if_stamp=original['stamp'])
        self.assertEqual(rewritten['content'], 'other')
        self.assertNotEqual(rewritten['stamp'], original['stamp'])
        replacement = self.root / 'replacement'
        replacement.write_bytes(b'other')
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(replacement, path)
        replaced = files.read(self.root, path.name, if_stamp=rewritten['stamp'])
        self.assertEqual(replaced['version'], rewritten['version'])
        self.assertNotEqual(replaced['stamp'], rewritten['stamp'])
        for expected in (original['version'], replaced['stamp']):
            with self.assertRaises(WorkspaceError) as error:
                files.write(self.root, path.name, 'draft', expected)
            self.assertEqual(error.exception.status, 409)
        self.assertEqual(files.write(self.root, path.name, 'saved', replaced['version'])['version'],
                         hashlib.sha256(b'saved').hexdigest())

    def test_file_racing_read_has_exact_sha_but_no_reusable_stamp(self):
        path = self.root / 'note.txt'
        path.write_bytes(b'first')
        open_regular = files.open_regular

        @contextmanager
        def race(file):
            with open_regular(file) as stream:
                read = stream.read
                def change_after_read(*args):
                    raw = read(*args)
                    path.write_bytes(b'changed while reading')
                    return raw
                with patch.object(stream, 'read', side_effect=change_after_read):
                    yield stream

        with patch.object(files, 'open_regular', side_effect=race):
            result = files.read(self.root, path.name)
        self.assertEqual(result['content'], 'first')
        self.assertEqual(result['version'], hashlib.sha256(b'first').hexdigest())
        self.assertNotIn('stamp', result)
        self.assertEqual(files.read(self.root, path.name)['content'], 'changed while reading')

    @unittest.skipUnless(files._STAT_HINTS_SUPPORTED, 'Stat-only hints require POSIX change-time semantics')
    def test_file_race_during_conditional_check_reopens_and_reads_full(self):
        path = self.root / 'note.txt'
        path.write_bytes(b'first')
        previous = files.read(self.root, path.name)
        fstat, calls = os.fstat, []
        def change_at_check(fd):
            calls.append(fd)
            # open_regular validation, pre-check, post-check
            if len(calls) == 3:
                path.write_bytes(b'new content')
            return fstat(fd)
        with patch.object(os, 'fstat', side_effect=change_at_check):
            result = files.read(self.root, path.name, if_stamp=previous['stamp'])
        self.assertGreaterEqual(len(calls), 6, 'fallback revalidates a newly pinned descriptor')
        self.assertEqual(result['content'], 'new content')
        self.assertEqual(files.read(self.root, path.name, if_stamp=result['stamp'])['unchanged'], True)

    def test_file_conditional_does_not_bypass_path_links_missing_or_limits(self):
        path = self.root / 'note.txt'
        path.write_bytes(b'first')
        previous = files.read(self.root, path.name)
        previous.setdefault('stamp', 'unsupported-stat-hint')
        with self.assertRaises(WorkspaceError):
            files.read(self.root, '../note.txt', if_stamp=previous['stamp'])
        path.unlink()
        with self.assertRaises(WorkspaceError) as error:
            files.read(self.root, path.name, if_stamp=previous['stamp'])
        self.assertEqual(error.exception.status, 404)
        path.write_bytes(b'too large')
        with patch.object(files, 'MAX_FILE', 4), self.assertRaises(WorkspaceError) as error:
            files.read(self.root, path.name, if_stamp=previous['stamp'])
        self.assertEqual(error.exception.status, 413)
        outside = self.root / 'outside'
        outside.write_bytes(b'first')
        path.unlink()
        os.link(outside, path)
        with self.assertRaises(WorkspaceError):
            files.read(self.root, path.name, if_stamp=previous['stamp'])
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest('Symlinks unavailable; other boundary checks passed')
        with self.assertRaises(WorkspaceError):
            files.read(self.root, path.name, if_stamp=previous['stamp'])

    def test_windows_policy_keeps_full_reads_even_for_an_identical_stat_hint(self):
        path = self.root / 'note.txt'; path.write_bytes(b'first')
        with patch.object(files, '_STAT_HINTS_SUPPORTED', False), patch.object(files, '_stat_stamp', return_value='creation-time-unchanged'):
            original = files.read(self.root, path.name)
            path.write_bytes(b'other')
            changed = files.read(self.root, path.name, if_stamp='creation-time-unchanged')
        self.assertNotIn('stamp', original); self.assertNotIn('unchanged', changed); self.assertNotIn('stamp', changed)
        self.assertEqual(changed['content'], 'other'); self.assertNotEqual(changed['version'], original['version'])

    def test_even_a_matching_hint_cannot_bypass_the_file_size_limit(self):
        path = self.root / 'large'; path.write_bytes(b'oversized')
        stamp = files._stat_stamp(path.stat())
        with patch.object(files, 'MAX_FILE', 4), self.assertRaises(WorkspaceError) as error:
            files.read(self.root, path.name, if_stamp=stamp)
        self.assertEqual(error.exception.status, 413)

    def test_tree_conditionals_preserve_all_entries_and_subtree_deletions(self):
        for directory in ('.git', '.beads', 'latex-workspace/.easyread-build'):
            folder = self.root / directory
            folder.mkdir(parents=True)
            (folder / 'hidden').write_text('visible')
        original = files.tree(self.root)
        self.assertEqual(len(original['entries']), 7)
        self.assertEqual(files.tree(self.root, if_version=original['version']),
                         {'version': original['version'], 'unchanged': True})
        subtree = files.tree(self.root, '.git')
        self.assertEqual(files.tree(self.root, '.git', if_version=original['version']), subtree)
        (self.root / '.git/hidden').unlink()
        changed = files.tree(self.root, '.git', if_version=subtree['version'])
        self.assertEqual(changed['entries'], [])
        self.assertNotEqual(files.tree(self.root, if_version=original['version'])['version'], original['version'])
        (self.root / '.git').rmdir()
        with self.assertRaises(WorkspaceError) as error:
            files.tree(self.root, '.git', if_version=changed['version'])
        self.assertEqual(error.exception.status, 404)


class ConditionalLatexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='easyread-latex-conditional-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine_patch = patch.object(latex, '_executable', return_value=None)
        self.engine_patch.start()
        self.addCleanup(self.engine_patch.stop)

    def test_latex_initialization_full_logs_settings_and_engines(self):
        self.assertFalse(latex.busy(self.root))
        self.assertFalse((self.root / 'latex-workspace').exists())
        initial = latex.initialize(self.root, if_version='unknown')
        source = self.root / 'latex-workspace/main.tex'
        self.assertTrue(source.is_file())
        self.assertEqual(latex.initialize(self.root, if_version=initial['version']),
                         {'version': initial['version'], 'unchanged': True})
        source.unlink()
        self.assertTrue(latex.initialize(self.root, if_version=initial['version'])['unchanged'])
        self.assertFalse(source.exists(), 'conditionals never recreate deleted starter sources')
        source.write_text('source')
        latex.settings(self.root, {'engine': 'xelatex'})
        settings = latex.initialize(self.root, if_version=initial['version'])
        self.assertEqual(settings['engine'], 'xelatex')
        state_path = source.parent / latex.STATE_FILE
        state = json.loads(state_path.read_text())
        log = 'full diagnostic log\n' * 30000
        state['compilation'] = {'state': 'error', 'log': log, 'errors': [{'line': 7, 'message': 'failure'}]}
        state_path.write_text(json.dumps(state))
        changed = latex.initialize(self.root, if_version=settings['version'])
        self.assertEqual(changed['compilation'], state['compilation'])
        self.assertLess(len(json.dumps(latex.initialize(self.root, if_version=changed['version']))), 110)
        with patch.object(latex, '_executable', return_value='/installed/tex'):
            engines = latex.initialize(self.root, if_version=changed['version'])
        self.assertTrue(all(engine['installed'] for engine in engines['engines']))
        self.assertEqual(engines['compilation']['log'], log)
        self.assertNotEqual(engines['version'], changed['version'])
        state_path.write_text('{invalid')
        with self.assertRaises(ValueError):
            latex.initialize(self.root, if_version=changed['version'])

    def test_latex_running_restart_and_pdf_disappearance_are_not_unchanged(self):
        initial = latex.initialize(self.root)
        root = self.root / 'latex-workspace'
        state = json.loads((root / latex.STATE_FILE).read_text())
        state['compilation'] = {'state': 'running', 'log': 'all output', 'errors': []}
        latex._save(root, state)
        with patch.dict(latex._JOBS, {str(root): object()}):
            running = latex.initialize(self.root, if_version=initial['version'])
            self.assertEqual(running['compilation']['state'], 'running')
            self.assertTrue(latex.initialize(self.root, if_version=running['version'])['unchanged'])
        interrupted = latex.initialize(self.root, if_version=running['version'])
        self.assertEqual(interrupted['compilation']['code'], 'interrupted')
        self.assertEqual(interrupted['compilation']['log'], 'all output')
        pdf = root / latex.BUILD_DIR / 'build' / 'main.pdf'
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b'%PDF-test')
        state['compilation'] = {'state': 'done', 'log': 'complete', 'errors': [],
                                'pdf': pdf.relative_to(self.root).as_posix(), 'synctex': True}
        latex._save(root, state)
        done = latex.initialize(self.root, if_version=interrupted['version'])
        self.assertEqual(done['compilation'], state['compilation'])
        pdf.unlink()
        missing = latex.initialize(self.root, if_version=done['version'])
        self.assertEqual(missing['compilation']['state'], 'error')
        self.assertNotIn('pdf', missing['compilation'])
        self.assertEqual(missing['compilation']['log'], 'complete')


if __name__ == '__main__':
    unittest.main()

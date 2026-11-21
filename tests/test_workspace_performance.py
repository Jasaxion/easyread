"""Conservative workspace optimizations: same data, less unnecessary work."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from easyread.library.library import Library
from easyread.library.store import write_json_atomic
from easyread.workspaces import WorkspaceError, beads_version
from easyread.workspaces.store import Store


class WorkspacePerformanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='easyread-performance-')
        self.root = Path(self.tmp.name)
        beads_version._CACHE.clear()

    def tearDown(self):
        beads_version._CACHE.clear()
        self.tmp.cleanup()

    def test_linked_only_preserves_linked_data_order_and_missing_references(self):
        store = Store(self.root)
        project = store.allocate({'name': 'Performance test'})
        for document_id in ('paper-zz', 'paper-aa', 'paper-unused'):
            folder = self.root / document_id; folder.mkdir()
            (folder / 'paper.json').write_text(json.dumps({'meta': {'title_en': document_id}, 'blocks': []}))
        project['papers'] = ['paper-zz', 'paper-missing', 'paper-aa']
        write_json_atomic(store.meta(project['id']) / 'project.json', project)
        before = store.papers(project['id'])
        with patch.object(Library, 'all', side_effect=AssertionError('sidebar must not scan the library')):
            after = store.papers(project['id'], linked_only=True)
        self.assertEqual(after['linked'], before['linked'])
        self.assertEqual([p['id'] for p in after['linked']], ['paper-aa', 'paper-zz', 'paper-missing'])
        self.assertEqual(after['available'], [])
        self.assertEqual([p['id'] for p in before['available']], ['paper-unused'])
        self.assertEqual(store.papers(project['id'], 'absent')['available'], [])

    def test_empty_linked_list_reads_no_papers(self):
        store = Store(self.root); project = store.allocate({'name': 'Empty'})
        with patch.object(Library, 'all', side_effect=AssertionError('unexpected scan')), patch.object(Library, 'summary', side_effect=AssertionError('unexpected summary')):
            self.assertEqual(store.papers(project['id'], linked_only=True), {'linked': [], 'available': []})

    def test_read_probes_are_cached_but_writes_always_probe(self):
        exe = self.root / 'bd'; exe.write_text('test executable')
        execute = Mock(return_value='bd version 1.3.4 (test)')
        for _ in range(3):
            self.assertEqual(beads_version.supported_version(str(exe), self.root, {}, execute), '1.3.4')
        self.assertEqual(execute.call_count, 1)
        beads_version.supported_version(str(exe), self.root, {}, execute, fresh=True)
        self.assertEqual(execute.call_count, 2)
        execute.return_value = 'bd version 9.9.9 (unsupported)'
        with self.assertRaises(WorkspaceError):
            beads_version.supported_version(str(exe), self.root, {}, execute, fresh=True)
        self.assertNotIn(str(exe), beads_version._CACHE)

    def test_atomic_binary_replacement_invalidates_probe_even_with_same_mtime(self):
        exe = self.root / 'bd'; exe.write_text('one')
        execute = Mock(return_value='bd version 1.3.1 (test)')
        beads_version.supported_version(str(exe), self.root, {}, execute)
        before = exe.stat(); replacement = self.root / 'replacement'; replacement.write_text('two')
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns)); os.replace(replacement, exe)
        execute.return_value = 'bd version 1.3.4 (test)'
        self.assertEqual(beads_version.supported_version(str(exe), self.root, {}, execute), '1.3.4')
        self.assertEqual(execute.call_count, 2)

    def test_probe_cache_expires_and_is_bounded(self):
        exe = self.root / 'bd'; exe.write_text('test')
        execute = Mock(return_value='bd version 1.3.4 (test)')
        with patch.object(beads_version.time, 'monotonic', return_value=100):
            beads_version.supported_version(str(exe), self.root, {}, execute)
        with patch.object(beads_version.time, 'monotonic', return_value=100 + beads_version._TTL + 1):
            beads_version.supported_version(str(exe), self.root, {}, execute)
        self.assertEqual(execute.call_count, 2)
        for i in range(beads_version._LIMIT + 2):
            candidate = self.root / str(i); candidate.write_text('test')
            beads_version.supported_version(str(candidate), self.root, {}, execute)
        self.assertEqual(len(beads_version._CACHE), beads_version._LIMIT)

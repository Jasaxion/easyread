"""Legacy snapshot decoding remains compatible; new links never copy content."""
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from easyread.workspaces import WorkspaceError, remote_snapshots
from tests import test_workspace_remote_http as fixture


class WorkspaceRemoteSnapshotAccountingTest(unittest.TestCase):
    def test_capture_counts_retained_utf8_representation_at_exact_limit(self):
        document_id = 'paper-test'
        api = Mock()
        api.document.return_value = {'id': document_id, 'title': '中文 and 日本語 🧪'}

        def page(document_id, *, limit, offset):
            # Metadata repeats in every page but is retained only once.
            return {'items': [{'id': str(offset), 'text': '中文🧪' * 100}], 'total': 2,
                    'offset': offset, 'limit': limit, 'next_offset': offset + 1,
                    'paper_note': {'content': '笔记' * 1000}}

        api.content.side_effect = api.notes.side_effect = api.highlights.side_effect = page
        with patch('easyread.workspaces.mcp.public_api', return_value=api), \
                patch.object(remote_snapshots, 'now_iso', return_value='2026-01-01T00:00:00Z'):
            snapshot = remote_snapshots.capture('/unused', document_id)
            retained_size = len(json.dumps({document_id: snapshot}, ensure_ascii=False).encode('utf-8'))
            with patch.object(remote_snapshots, 'MAX_SNAPSHOT', retained_size):
                self.assertEqual(remote_snapshots.capture('/unused', document_id), snapshot)
            with patch.object(remote_snapshots, 'MAX_SNAPSHOT', retained_size - 1):
                with self.assertRaises(WorkspaceError) as failure:
                    remote_snapshots.capture('/unused', document_id)
                self.assertEqual(failure.exception.status, 413)


class WorkspaceRemoteReferenceHTTPTest(unittest.TestCase):
    setUp = fixture.WorkspaceRemoteHTTPTest.setUp
    tearDown = fixture.WorkspaceRemoteHTTPTest.tearDown
    setUp_local = fixture.WorkspaceRemoteHTTPTest.setUp_local
    tearDown_local = fixture.WorkspaceRemoteHTTPTest.tearDown_local
    request = fixture.WorkspaceRemoteHTTPTest.request
    login = fixture.WorkspaceRemoteHTTPTest.login
    allocated = fixture.WorkspaceRemoteHTTPTest.allocated

    def test_multilingual_large_papers_link_without_transfer_or_snapshot_limit(self):
        pid, prefix = self.allocated()
        document_id = 'local-paper'
        document = Path(self.tmp.name) / document_id
        document.mkdir()

        def paper(text):
            (document / 'paper.json').write_text(json.dumps({'meta': {'title_en': 'Multilingual paper'},
                'blocks': [{'id': 'p1', 'type': 'paragraph', 'en': text}]}, ensure_ascii=False), encoding='utf-8')

        # 4.5 MB UTF-8, but over 9 MB with ensure_ascii=True.
        text = '中' * 1_500_000 + ' 日本語 / English / 🧪'
        paper(text)
        status, _, result = self.request(prefix + '/papers/link', {'document_id': document_id})
        self.assertEqual(status, 200, result)
        root = self.remote_root / 'workspace' / pid
        path = root / '.easyread-papers.json'
        retained = path.read_bytes()
        self.assertLess(len(retained), 1024)
        self.assertFalse((root / remote_snapshots.SNAPSHOT_FILE).exists())
        self.assertEqual(json.loads(retained), {'papers': [document_id]})

        paper('中' * (remote_snapshots.MAX_SNAPSHOT // 3 + 1))
        status, _, result = self.request(prefix + '/papers/link', {'document_id': document_id})
        self.assertEqual(status, 200, result)
        self.assertEqual(path.read_bytes(), retained)
        self.assertEqual(self.manager.registry.project(pid)['papers'], [document_id])


if __name__ == '__main__':
    unittest.main()

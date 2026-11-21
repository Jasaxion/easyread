"""Explicit, bounded paper snapshots; never a tunnel back to the local library."""
from __future__ import annotations

from copy import deepcopy
import json

from ..library.store import now_iso, write_json_atomic
from . import WorkspaceError, files
from .store import Store

SNAPSHOT_FILE = '.easyread-paper-snapshots.json'
MAX_SNAPSHOT = files.MAX_TEXT


def capture(libroot, document_id):
    from .mcp import public_api
    api = public_api(libroot)
    try:
        snapshot = {'document': api.document(document_id), 'captured_at': now_iso()}
        size = len(json.dumps({document_id: snapshot}, ensure_ascii=False).encode('utf-8'))
        for field in ('content', 'notes', 'highlights'):
            result, items, offset = None, [], 0
            while True:
                page = getattr(api, field)(document_id, limit=200, offset=offset)
                if result is None:
                    result = {**page, 'items': items, 'offset': 0, 'limit': 0, 'next_offset': None}
                    snapshot[field] = result
                    # Add the retained dict entry (comma-space, key, colon-space,
                    # value). Repeated page metadata is not stored or counted.
                    size += 4 + len(json.dumps(field).encode()) + len(json.dumps(result, ensure_ascii=False).encode('utf-8'))
                for item in page['items']:
                    size += (2 if items else 0) + len(json.dumps(item, ensure_ascii=False).encode('utf-8'))
                    items.append(item)
                size += len(str(len(items))) - len(str(result['limit']))
                result['limit'] = len(items)
                if size > MAX_SNAPSHOT:
                    raise WorkspaceError('Paper snapshot exceeds 8 MiB; no partial snapshot was transferred', 413)
                offset += len(page['items'])
                if offset >= page['total'] or not page['items']:
                    break
        return snapshot
    finally:
        api.close()


class SnapshotStore(Store):
    def create(self, body):
        result = super().create(body)
        root = self.root(result['project']['id'])
        instructions = ('\n## Remote paper boundary\n\n'
                        'EasyRead MCP reads only explicitly linked paper snapshots, not the live local library. '
                        'Ask the user to link or relink a paper in EasyRead to transfer or refresh effective text, '
                        'notes and highlights. Snapshots keep source IDs/links; figures and attachments are not copied. '
                        'Never claim the local library was searched or that snapshots are current.\n')
        for name in ('AGENTS.md', 'CLAUDE.md', 'README.md'):
            with (root / name).open('a', encoding='utf-8') as stream:
                stream.write(instructions)
        return result

    def allocate(self, body, *, project_id=None):
        project = super().allocate(body, project_id=project_id or body.get('_allocation_id'))
        project['paper_source'] = 'snapshot'
        write_json_atomic(self.meta(project['id']) / 'project.json', project)
        return project

    def snapshots(self, pid):
        path = self.root(pid) / SNAPSHOT_FILE
        if not path.exists() and not path.is_symlink():
            return {}
        raw = files.read_bytes(path, MAX_SNAPSHOT)
        if len(raw) > MAX_SNAPSHOT:
            raise WorkspaceError('Paper snapshots exceed 8 MiB', 413)
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except ValueError:
            raise WorkspaceError('Paper snapshots are unreadable', 409) from None

    def papers(self, pid, q='', *, linked_only=False):
        snapshots = self.snapshots(pid)
        return {'linked': [snapshots[x]['document'] if x in snapshots else {'id': x, 'missing': True}
                           for x in self.get(pid).get('papers', [])], 'available': [], 'mode': 'snapshot'}

    def link(self, pid, document_id, remove=False):
        if not remove:
            raise WorkspaceError('Link or refresh papers from the EasyRead library panel; only linked snapshots are available remotely', 409, 'remote_snapshot_required')
        return self.install_snapshot(pid, document_id, None)

    def install_snapshot(self, pid, document_id, snapshot):
        if not isinstance(document_id, str) or not document_id or len(document_id) > 200:
            raise WorkspaceError('Invalid document ID')
        if snapshot is not None:
            if not isinstance(snapshot, dict) or any(not isinstance(snapshot.get(k), dict) for k in ('document', 'content', 'notes', 'highlights')) or snapshot['document'].get('id') != document_id:
                raise WorkspaceError('Invalid paper snapshot')
        with self.lock(pid):
            project = self.get(pid)
            snapshots = self.snapshots(pid)
            if snapshot is None:
                snapshots.pop(document_id, None)
            else:
                snapshots[document_id] = snapshot
            content = json.dumps(snapshots, ensure_ascii=False)
            if len(content.encode()) > MAX_SNAPSHOT:
                raise WorkspaceError('Linked paper snapshots exceed the 8 MiB project limit', 413)
            # File helpers enforce no-follow boundaries, including existing files.
            path = self.root(pid) / SNAPSHOT_FILE
            if path.exists() or path.is_symlink():
                current = files.read(self.root(pid), SNAPSHOT_FILE)
                files.write(self.root(pid), SNAPSHOT_FILE, content, current['version'])
            else:
                files.create(self.root(pid), SNAPSHOT_FILE, content=content)
            ids = project.get('papers', [])
            ids = [x for x in ids if x != document_id] if snapshot is None else list(dict.fromkeys([*ids, document_id]))
            project.update(papers=ids, updated=now_iso())
            write_json_atomic(self.meta(pid) / 'project.json', project)
            return {'project': project, **self.papers(pid)}


class SnapshotAPI:
    """Same read shape as PublicAPI, but only explicitly transferred snapshots."""
    def __init__(self, store, pid):
        self.store, self.pid = store, pid

    def _snapshot(self, document_id):
        snapshot = self.store.snapshots(self.pid).get(document_id)
        if not snapshot or document_id not in self.store.get(self.pid).get('papers', []):
            raise WorkspaceError('Paper has no linked snapshot. Link it in EasyRead first.', 404)
        return deepcopy(snapshot)

    def documents(self, q='', category='', status='', limit=50, offset=0):
        from ..server.public_api import page
        items = self.store.papers(self.pid)['linked']
        if q:
            items = [x for x in items if q.casefold() in json.dumps(x, ensure_ascii=False).casefold()]
        if category:
            items = [x for x in items if category in x.get('tags', [])]
        if status:
            items = [x for x in items if x.get('status') == status]
        return {**page(items, limit, offset), 'source': 'linked_snapshots'}

    def document(self, document_id):
        return self._snapshot(document_id)['document']

    def _page(self, document_id, field, limit, offset):
        from ..server.public_api import page
        snapshot = self._snapshot(document_id)
        data = snapshot[field]
        return {**data, **page(data['items'], limit, offset), 'source': 'linked_snapshot', 'captured_at': snapshot['captured_at']}

    def content(self, document_id, language='both', view='effective', limit=50, offset=0):
        if language not in ('original', 'translation', 'both') or view != 'effective':
            raise WorkspaceError('Snapshots contain effective content only; language must be original, translation or both')
        result = self._page(document_id, 'content', limit, offset)
        if language != 'both':
            for block in result['items']:
                for key in (('zh', 'caption_zh', 'image_zh') if language == 'original' else ('en', 'caption_en', 'image_en')):
                    block.pop(key, None)
                for item in block.get('items', []):
                    item.pop('zh' if language == 'original' else 'en', None)
        result['language'] = language
        return result

    def notes(self, document_id, limit=50, offset=0):
        return self._page(document_id, 'notes', limit, offset)

    def highlights(self, document_id, limit=50, offset=0):
        return self._page(document_id, 'highlights', limit, offset)

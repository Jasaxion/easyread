"""Remote paper references. Live content comes from the user's native API/MCP
configuration with the existing full EasyRead access code, not a scoped proxy.
"""
from __future__ import annotations

import hashlib
import json
import re

from ..library.store import now_iso, write_json_atomic
from . import WorkspaceError, files
from .store import Store

LINKS_FILE = '.easyread-papers.json'
INSTRUCTIONS = '''
## EasyRead library access on this remote host

Use your native Agent CLI's configured EasyRead REST API or Streamable HTTP MCP
endpoint to search the entire library and read current content, notes and
highlights. The existing global EasyRead access code is the API Bearer token;
there is no additional project-scoped library restriction. Configure the service
URL and full token in the remote account's native CLI settings/environment, not
in project files. Ask the user to configure access if it is missing; never
invent sources or silently use old paper snapshots as current content.
The source of truth for linked references is .easyread-papers.json, formatted as
{"papers": ["document-id", ...]}. Preserve existing IDs when adding papers;
write the complete JSON atomically. Links do not copy papers or grant access.
'''


class ReferenceStore(Store):
    def allocate(self, body, *, project_id=None):
        project = super().allocate(body, project_id=project_id or body.get('_allocation_id'))
        project['paper_source'] = 'native_api'
        write_json_atomic(self.meta(project['id']) / 'project.json', project)
        return project

    def create(self, body):
        result = super().create(body)
        root = self.root(result['project']['id'])
        for name in ('AGENTS.md', 'CLAUDE.md', 'README.md'):
            with (root / name).open('a', encoding='utf-8') as stream:
                stream.write(INSTRUCTIONS)
        files.create(root, LINKS_FILE, content=json.dumps({'papers': []}))
        return result

    def _manifest(self, project):
        path = files.target(self.root(project['id']), LINKS_FILE)
        if not path.exists():  # Older projects may have only private metadata.
            return {'papers': list(project.get('papers', []))}, None
        raw = files.read_bytes(path, files.MAX_TEXT)
        try:
            data = json.loads(raw)
            ids = data['papers']
            if not isinstance(ids, list) or not all(isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_-]{4,64}', x) for x in ids):
                raise ValueError()
        except (ValueError, TypeError, KeyError, RecursionError):
            raise WorkspaceError('Invalid .easyread-papers.json: expected {"papers": ["document-id", ...]}; file not modified',
                                 409, 'paper_manifest_invalid') from None
        data['papers'] = list(dict.fromkeys(ids))
        return data, hashlib.sha256(raw).hexdigest()

    def describe(self, pid):
        project = self.get(pid)
        data, _ = self._manifest(project)
        return {**project, 'papers': data['papers']}

    def papers(self, pid, q='', *, linked_only=False):
        project = self.describe(pid)
        return {'project': project, 'linked': [{'id': value} for value in project['papers']],
                'available': [], 'mode': 'native_api'}

    def link(self, pid, document_id, remove=False):
        # The authenticated deployment adapter validates library existence.
        # This worker stores IDs only; it never receives a user API credential.
        if not isinstance(document_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{4,64}', document_id):
            raise WorkspaceError('Invalid document ID')
        with self.lock(pid):
            project = self.get(pid)
            data, version = self._manifest(project)
            ids = data['papers']
            ids = [value for value in ids if value != document_id] if remove else list(dict.fromkeys([*ids, document_id]))
            content = json.dumps({**data, 'papers': ids}, ensure_ascii=False)
            root = self.root(pid)
            if version is not None:
                files.write(root, LINKS_FILE, content, version)
            else:
                files.create(root, LINKS_FILE, content=content)
            project.update(papers=ids, paper_source='native_api', updated=now_iso())
            write_json_atomic(self.meta(pid) / 'project.json', project)
        return {'project': project, 'linked': [{'id': value} for value in ids], 'available': [], 'mode': 'native_api'}

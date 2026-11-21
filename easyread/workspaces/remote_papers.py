"""Remote paper references. Live content comes from the user's native API/MCP
configuration with the existing full EasyRead access code, not a scoped proxy.
"""
from __future__ import annotations

import json

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
Linked paper IDs are in .easyread-papers.json. References do not copy papers.
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

    def papers(self, pid, q='', *, linked_only=False):
        return {'linked': [{'id': value} for value in self.get(pid).get('papers', [])],
                'available': [], 'mode': 'native_api'}

    def link(self, pid, document_id, remove=False):
        # The authenticated deployment adapter validates library existence.
        # This worker stores IDs only; it never receives a user API credential.
        if not isinstance(document_id, str) or not document_id or len(document_id) > 200:
            raise WorkspaceError('Invalid document ID')
        with self.lock(pid):
            project = self.get(pid)
            ids = project.get('papers', [])
            ids = [value for value in ids if value != document_id] if remove else list(dict.fromkeys([*ids, document_id]))
            content = json.dumps({'papers': ids}, ensure_ascii=False)
            root = self.root(pid)
            if (root / LINKS_FILE).exists() or (root / LINKS_FILE).is_symlink():
                current = files.read(root, LINKS_FILE)
                files.write(root, LINKS_FILE, content, current['version'])
            else:
                files.create(root, LINKS_FILE, content=content)
            project.update(papers=ids, paper_source='native_api', updated=now_iso())
            write_json_atomic(self.meta(pid) / 'project.json', project)
        return {'project': project, **self.papers(pid)}

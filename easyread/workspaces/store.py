"""Project identity/metadata and reference-only links to the existing library."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import shutil
import string
import subprocess
import threading

from filelock import FileLock, Timeout

from ..library.library import Library
from ..library.store import now_iso, read_json, write_json_atomic
from . import WorkspaceError
from . import files, git

_LOCKS = {}
_LOCK = threading.RLock()
_ID = re.compile(r'^[a-z]{20}$')


class _ProjectLock:
    # Stdio MCP tools run in separate processes; a thread-only lock loses links
    # when a browser edits project metadata concurrently. FileLock is reentrant.
    def __init__(self, path):
        self.thread = threading.RLock()
        self.file = FileLock(path, timeout=15)

    def __enter__(self):
        self.thread.acquire()
        try:
            self.file.acquire()
        except Exception as exc:
            self.thread.release()
            if isinstance(exc, Timeout):
                raise WorkspaceError('Project is busy; retry after the current operation', 409) from None
            raise
        return self

    def __exit__(self, *args):
        self.file.release()
        self.thread.release()


def capabilities():
    bead = shutil.which(os.environ.get('EASYREAD_BEADS_COMMAND', 'bd'))
    return {'git': bool(git.executable()), 'beads': bool(bead),
            'terminal': {'available': bool(shutil.which('ttyd')), 'reason': '' if shutil.which('ttyd') else 'Install ttyd to use native CLI terminals'}}


class Store:
    def __init__(self, libroot):
        # A configured library location is trusted; canonicalize its ancestors
        # (e.g. macOS /var) before enforcing no-follow boundaries below it.
        self.libroot = Path(libroot).expanduser().resolve()
        self.base = self.libroot / 'workspace'
        self.metadata = self.libroot / '.research-workspaces'

    def _id(self, pid):
        if not isinstance(pid, str) or not _ID.fullmatch(pid):
            raise WorkspaceError('Invalid project ID', 404)
        return pid

    def lock(self, pid):
        key = (str(self.libroot.resolve()), self._id(pid))
        with _LOCK:
            return _LOCKS.setdefault(key, _ProjectLock(self.meta(pid) / '.project.lock'))

    def meta(self, pid):
        pid = self._id(pid)
        if self.metadata.is_symlink() or (self.metadata / pid).is_symlink():
            raise WorkspaceError('Invalid project metadata directory', 403)
        return self.metadata / pid

    def root(self, pid):
        pid = self._id(pid)
        if self.base.is_symlink() or (self.base / pid).is_symlink():
            raise WorkspaceError('Invalid workspace directory', 403)
        root = self.base / pid
        if not root.is_dir() or not (self.meta(pid) / 'project.json').is_file():
            raise WorkspaceError('Project not found', 404)
        return root

    def get(self, pid):
        self.root(pid)
        project = read_json(self.meta(pid) / 'project.json', None)
        if not isinstance(project, dict) or project.get('id') != pid:
            raise WorkspaceError('Project metadata is unavailable', 500)
        return project

    def describe(self, pid):
        """Project metadata for display/export, including the effective references."""
        return self.get(pid)

    def listing(self, query=''):
        out = []
        if self.metadata.is_dir() and not self.metadata.is_symlink():
            for folder in self.metadata.iterdir():
                if not _ID.fullmatch(folder.name):
                    continue
                try:
                    item = self.get(folder.name)
                except WorkspaceError:
                    continue
                if str(query).casefold() in (item['name'] + ' ' + item['description']).casefold():
                    out.append(item)
        return sorted(out, key=lambda x: (x.get('updated', ''), x['id']), reverse=True)

    @staticmethod
    def validate(body):
        name, description = body.get('name'), body.get('description', '')
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise WorkspaceError('Project name is required (maximum 200 characters)')
        if not isinstance(description, str) or len(description) > 10000:
            raise WorkspaceError('Project description is too long')
        return name.strip(), description

    def allocate(self, body, *, project_id=None):
        name, description = self.validate(body)
        engine = body.get('engine', 'codex')
        if engine not in ('codex', 'claude', 'pi'):
            raise WorkspaceError('Choose Codex, Claude Code or Pi Agent')
        if self.base.is_symlink() or self.metadata.is_symlink():
            raise WorkspaceError('Invalid workspace base directory', 403)
        self.base.mkdir(parents=True, exist_ok=True)
        self.metadata.mkdir(parents=True, exist_ok=True)
        while True:
            pid = self._id(project_id) if project_id is not None else ''.join(secrets.choice(string.ascii_lowercase) for _ in range(20))
            if not (self.base / pid).exists() and not (self.metadata / pid).exists():
                break
            if project_id is not None:
                raise WorkspaceError('Project allocation already exists; inspect it before retrying', 409, 'remote_conflict')
        (self.base / pid).mkdir(mode=0o700)
        self.meta(pid).mkdir(mode=0o700)
        project = {'id': pid, 'name': name, 'description': description, 'engine': engine,
                   'created': now_iso(), 'updated': now_iso(), 'papers': [], 'initialization': {}}
        write_json_atomic(self.meta(pid) / 'project.json', project)
        return project

    def create(self, body):
        project = self.allocate(body)
        pid, name = project['id'], project['name']
        root = self.root(pid)
        try:
            (root / 'README.md').write_text(f'# {name}\n\n{project["description"]}\n\nResearch files live here. Linked papers remain in the EasyRead library.\n', encoding='utf-8')
            instructions = ('# Research workspace\n\n'
                'Work in this directory. Preserve user files and uncommitted changes.\n'
                'Use the easyread MCP tools to search/read library documents, notes and highlights and link relevant papers to this project.\n'
                'Cite paper IDs and source block IDs. Do not invent evidence.\n'
                'Use latex-workspace for LaTeX sources and build outputs.\n'
                'Do not commit, push, publish, or delete unrelated files unless the user explicitly asks.\n\n'
                '## Task tracking (Beads)\n\n'
                'Use the initialized .beads workspace in this directory. At session start and after context reset, run `bd prime` for the current native workflow.\n'
                'Use `bd ready` to find work, `bd show <id>` for context, `bd update <id> --claim` to claim it, and `bd close <id>` when verified complete.\n'
                'Create missing work with `bd create`; use `bd remember` for persistent project knowledge.\n'
                'The Beads database is authoritative. Do not edit JSONL as a database or reinitialize an existing workspace.\n')
            for file in ('AGENTS.md', 'CLAUDE.md'):
                (root / file).write_text(instructions, encoding='utf-8')
            (root / '.gitignore').write_text('latex-workspace/*.aux\nlatex-workspace/*.log\nlatex-workspace/*.fls\nlatex-workspace/*.fdb_latexmk\nlatex-workspace/*.synctex.gz\n', encoding='utf-8')
            initialized = {}
            try:
                git.initialize(root)
                initialized['git'] = {'ok': True}
            except (WorkspaceError, OSError) as exc:
                initialized['git'] = {'ok': False, 'error': str(exc)}
            bead = shutil.which(os.environ.get('EASYREAD_BEADS_COMMAND', 'bd'))
            if bead and initialized['git']['ok']:
                env = git.environment()
                env.update(BD_NON_INTERACTIVE='1', BEADS_NO_DAEMON='1')
                try:
                    proc = subprocess.run([bead, 'init', '--non-interactive', '--prefix', 'research', '--skip-hooks', '--skip-agents'],
                                          cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=90)
                    # Older supported beads versions may not have --skip-hooks.
                    if proc.returncode and b'unknown flag: --skip-hooks' in proc.stderr:
                        proc = subprocess.run([bead, 'init', '--non-interactive', '--prefix', 'research'],
                                              cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=90)
                    initialized['beads'] = {'ok': proc.returncode == 0, 'error': (proc.stderr + proc.stdout).decode(errors='replace')[-4000:] if proc.returncode else ''}
                except (OSError, subprocess.TimeoutExpired) as exc:
                    initialized['beads'] = {'ok': False, 'error': str(exc)}
            else:
                initialized['beads'] = {'ok': False, 'error': 'Install beads (bd) and Git to initialize task tracking'}
            project['initialization'] = initialized
            write_json_atomic(self.meta(pid) / 'project.json', project)
            return {'project': project}
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(self.meta(pid), ignore_errors=True)
            raise

    def update(self, pid, body):
        with self.lock(pid):
            project = self.describe(pid)
            name, description = self.validate({**project, **body})
            project.update(name=name, description=description, updated=now_iso())
            write_json_atomic(self.meta(pid) / 'project.json', project)
            return {'project': project}

    def delete(self, pid):
        with self.lock(pid):
            root = self.root(pid)
            shutil.rmtree(root)
            shutil.rmtree(self.meta(pid))
            return {'ok': True}

    def papers(self, pid, q='', *, linked_only=False):
        ids = self.get(pid).get('papers', [])
        lib = Library(self.libroot)
        if linked_only:
            # Sidebar polling never needs candidates or summaries for unrelated papers.
            # Preserve the original filename ordering and missing-reference behavior.
            selected = {document_id: lib.ws(document_id) for document_id in ids}
            linked = [lib.summary(selected[document_id], repair=False) for document_id in sorted(selected) if selected[document_id] is not None]
            linked.extend({'id': document_id, 'display_title': document_id, 'missing': True}
                          for document_id in ids if selected[document_id] is None)
            return {'linked': linked, 'available': []}
        linked, available = [], []
        for ws in lib.all():
            item = lib.summary(ws, repair=False)
            if ws.id in ids:
                linked.append(item)
            elif str(q).casefold() in ' '.join(str(item.get(k, '')) for k in ('display_title', 'title_en', 'title_zh', 'authors', 'abstract', 'tags')).casefold():
                available.append(item)
        found = {x['id'] for x in linked}
        linked.extend({'id': pid, 'display_title': pid, 'missing': True} for pid in ids if pid not in found)
        return {'linked': linked, 'available': available[:200]}

    def link(self, pid, document_id, remove=False):
        if not isinstance(document_id, str):
            raise WorkspaceError('Document ID required')
        with self.lock(pid):
            project = self.get(pid)
            ids = project.get('papers', [])
            if remove:
                ids = [x for x in ids if x != document_id]
            elif document_id not in ids:
                if Library(self.libroot).ws(document_id) is None:
                    raise WorkspaceError('Document not found', 404)
                ids.append(document_id)
            project.update(papers=ids, updated=now_iso())
            write_json_atomic(self.meta(pid) / 'project.json', project)
        return self.papers(pid)

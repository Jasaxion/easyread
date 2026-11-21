"""Readiness checks shared by the SSH worker and the user-run check command."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile

from ..library.store import now_iso
from . import WorkspaceError, latex, session_cli, session_terminal, beads_cli, beads_version, git
from .remote_transport import PROTOCOL, build_id

LIBRARY_REASON = 'Configure the remote Agent CLI with the EasyRead HTTP API/MCP URL and the existing full access code. Whole-library reads use the native client configuration, not snapshots or project-scoped tokens.'


def capabilities():
    return {'git': bool(shutil.which('git')), 'beads': bool(shutil.which('bd')),
            'latex': latex.capabilities(),
            'terminal': {'available': session_terminal.enabled(), 'reason': session_terminal.unavailable_reason()},
            'linked_papers': {'mode': 'native_api', 'reason': LIBRARY_REASON}, 'remote': True}


def check(workspace, engine='codex'):
    if engine not in session_cli.ENGINES:
        raise WorkspaceError('Choose Codex, Claude Code or Pi Agent')
    checks = []

    def add(name, ok, message, required=True):
        checks.append({'name': name, 'ok': bool(ok), 'required': required, 'message': message})

    add('python', sys.version_info >= (3, 10), 'Python 3.10+ required; running ' + sys.version.split()[0])
    add('protocol', True, f'EasyRead SSH worker protocol {PROTOCOL}, build {build_id()}; install the same checkout as the client')
    missing = [name for name in ('filelock', 'mcp', 'PIL') if importlib.util.find_spec(name) is None]
    add('runtime', not missing, 'Install this EasyRead checkout with python -m pip install -e .' if missing else 'EasyRead runtime dependencies available')
    root = Path(workspace)
    writable = False
    try:
        if not root.is_absolute() or str(root) == '/' or any(p.is_symlink() for p in (root, *root.parents)) or not root.is_dir():
            raise OSError()
        with tempfile.TemporaryFile(dir=root):
            pass
        writable = os.access(root, os.W_OK | os.X_OK)
    except OSError:
        pass
    add('workspace', writable, f'Workspace must already exist, be writable and absolute, without symlink ancestors: {workspace}')
    def executable(name, found, args, help_text):
        if not found:
            add(name, False, help_text)
            return
        try:
            if name == 'bd':
                version = beads_version.supported_version(found, None, git.environment(), beads_cli.execute, fresh=True)
            else:
                version = beads_cli.execute([found, *args], None, git.environment()).strip().splitlines()[0][:200]
            add(name, True, f'{found}: {version}')
        except (WorkspaceError, IndexError, OSError):
            add(name, False, help_text + '; executable could not report a supported version')

    executable('git', git.executable(), ['--version'], 'Install runnable Git')
    executable('bd', shutil.which(os.environ.get('EASYREAD_BEADS_COMMAND', 'bd')), ['--version'],
               'Install supported bd 1.3.1–1.3.4 (pinned official 1.3.4): python scripts/install_workspace_deps.py --install')
    executable('dolt', shutil.which('dolt'), ['version'], 'Install Dolt required by Beads')
    executable('latexmk', shutil.which('latexmk'), ['-v'], 'Install latexmk (TeX Live or MiKTeX)')
    executable('pdflatex', latex._executable('pdflatex'), ['--version'],
               'Install a TeX distribution with pdflatex, the default project engine')
    executable('agent', session_cli.executable(engine), ['--version'],
               f'Install {session_cli.SPECS[engine]["name"]} and authenticate on this remote host')
    add('ttyd', session_terminal.ttyd_available(), 'Optional remote ttyd; enable EASYREAD_TERMINAL=1 on both hosts for embedded terminals', required=False)
    add('terminal', session_terminal.enabled(), session_terminal.unavailable_reason() or 'Remote embedded native terminals enabled', required=False)
    return {'ok': all(item['ok'] for item in checks if item['required']), 'checks': checks,
            'capabilities': capabilities(), 'checked_at': now_iso(), 'engine': engine}

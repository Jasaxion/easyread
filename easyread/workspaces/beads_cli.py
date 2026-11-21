"""Restricted native Beads transport for project-local embedded databases.

This is not a sandbox against an already-running, full-permission native Agent.
No project configuration is rewritten while such an Agent may be using it.
"""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading

from . import WorkspaceError, files, git
from .beads_version import supported_version

MAX_OUTPUT = 4 * 1024 * 1024
TIMEOUT = 25


def _unsafe(message):
    raise WorkspaceError(message, 409, 'beads_unsafe_config')


def validate(root):
    beads = files.target(root, '.beads', missing=False)
    if not beads.is_dir():
        _unsafe('Beads must be a local .beads directory')
    # Imported metadata cannot introduce filesystem indirection, even in native
    # Dolt metadata. Recheck per HTTP operation; do not cache this validation.
    count = 0
    for folder, dirs, names in os.walk(beads, followlinks=False):
        for name in dirs + names:
            path = Path(folder) / name
            mode = path.lstat()
            count += 1
            if count > 50000:
                _unsafe('Beads metadata is too large for the project panel')
            if not (stat.S_ISREG(mode.st_mode) or stat.S_ISDIR(mode.st_mode)) or (stat.S_ISREG(mode.st_mode) and mode.st_nlink != 1):
                _unsafe('Beads links and special files are not supported')
    for name in ('redirect', 'routes.jsonl', '.env', 'config.json'):
        if (beads / name).exists():
            _unsafe('Project panel does not support .beads/' + name + '; inspect it with native bd')
    try:
        metadata = json.loads(files.read_bytes(beads / 'metadata.json', 65536))
    except (FileNotFoundError, ValueError):
        _unsafe('Beads metadata is missing or invalid; initialize or repair it explicitly with native bd')
    allowed = {'database', 'backend', 'dolt_mode', 'dolt_database', 'project_id',
               'last_bd_version', 'deletions_retention_days', 'stale_closed_issues_days'}
    if not isinstance(metadata, dict) or set(metadata) - allowed:
        _unsafe('Project panel only supports local embedded Beads metadata, not custom paths or servers')
    if metadata.get('database') != 'dolt' or metadata.get('backend') != 'dolt' or metadata.get('dolt_mode') != 'embedded':
        _unsafe('Project panel requires an existing embedded Dolt Beads database')
    database = metadata.get('dolt_database', '')
    if not isinstance(database, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,99}', database):
        _unsafe('Invalid Beads database name')
    if not (beads / 'embeddeddolt' / database / '.dolt').is_dir():
        _unsafe('Embedded Beads database is missing; the panel will not initialize a new database')
    if not (beads / 'config.yaml').is_file():
        _unsafe('Project-local .beads/config.yaml is required; parent configuration must not be inherited')
    # Default bd init config is comments only. Deliberately accept a small scalar
    # subset rather than pretending to safely interpret arbitrary YAML/plugins.
    for name in ('config.yaml', 'config.local.yaml'):
        path = beads / name
        if not path.exists():
            continue
        for line in files.read_bytes(path, 65536).decode('utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if not re.fullmatch(r'(issue-prefix|actor|json|no-hooks|no-push):\s*[A-Za-z0-9_. -]*(?:\s+#.*)?', line):
                _unsafe('Unsupported Beads configuration in ' + name + '; use native bd to inspect custom settings')
    git.validate_metadata(root)
    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read_string(files.read_bytes(root / '.git/config', 65536).decode('utf-8'))
    except (ValueError, configparser.Error):
        _unsafe('Invalid project Git configuration')
    safe_keys = {
        'core': {'repositoryformatversion', 'bare', 'filemode', 'logallrefupdates', 'ignorecase', 'precomposeunicode', 'symlinks', 'longpaths', 'autocrlf', 'eol', 'quotepath'},
        'user': {'name', 'email'}, 'beads': {'role'},
    }
    for section in parser.sections():
        keys = safe_keys.get(section.lower())
        if re.fullmatch(r'remote "[\w.-]+"', section):
            keys = {'url', 'fetch', 'pushurl'}
        elif re.fullmatch(r'branch "[\w./-]+"', section):
            keys = {'remote', 'merge'}
        if keys is None or set(parser.options(section)) - keys or any('\n' in value or '\\' in value for _, value in parser.items(section)):
            _unsafe('Custom Git hooks, filters, includes or commands are not supported by the Beads panel')
    return beads


def environment(root, home):
    # Whitelist, rather than inheriting BD/BEADS/DOLT/GIT overrides, credentials,
    # runtime loaders or host user-level Beads configuration.
    env = {key: value for key, value in os.environ.items() if key in {
        'PATH', 'SYSTEMROOT', 'SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT', 'LANG', 'LC_ALL', 'TMPDIR', 'TEMP', 'TMP',
    }}
    env['PATH'] = os.pathsep.join(entry for entry in env.get('PATH', os.defpath).split(os.pathsep) if os.path.isabs(entry))
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home), LOCALAPPDATA=str(home),
               XDG_CONFIG_HOME=str(home), XDG_DATA_HOME=str(home), XDG_CACHE_HOME=str(home),
               DOLT_ROOT_PATH=str(home), USER='EasyRead', LOGNAME='EasyRead', PWD=str(root),
               BEADS_DIR=str(root / '.beads'), BEADS_DB=str(root / '.beads/dolt'), BD_DB=str(root / '.beads/dolt'),
               BD_NON_INTERACTIVE='1', BD_NO_HOOKS='true', BD_NO_PUSH='true', BD_IMPORT_AUTO='false',
               BD_EXPORT_AUTO='false', BD_EXPORT_GIT_ADD='false', BD_BACKUP_ENABLED='false',
               BD_EVENTS_EXPORT='false', BD_DISABLE_METRICS='1', DO_NOT_TRACK='1',
               BD_ROUTING_MODE='manual', BD_ROUTING_DEFAULT='.', BD_ROUTING_MAINTAINER='.', BD_ROUTING_CONTRIBUTOR='.',
               BD_CONTRIBUTOR_AUTO_ROUTE='false', BD_CONTRIBUTOR_PLANNING_REPO='.',
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_ATTR_NOSYSTEM='1', GIT_DIR=str(root / '.git'),
               GIT_COMMON_DIR=str(root / '.git'), GIT_WORK_TREE=str(root), NO_COLOR='1')
    overrides = {'core.hooksPath': os.devnull, 'core.fsmonitor': 'false', 'credential.helper': '',
                 'protocol.allow': 'never', 'core.pager': 'cat', 'gc.auto': '0', 'submodule.recurse': 'false',
                 'diff.ignoreSubmodules': 'all', 'core.attributesFile': os.devnull, 'core.sshCommand': 'false'}
    env['GIT_CONFIG_COUNT'] = str(len(overrides))
    for i, (key, value) in enumerate(overrides.items()):
        env['GIT_CONFIG_KEY_' + str(i)] = key
        env['GIT_CONFIG_VALUE_' + str(i)] = value
    return env


def execute(command, root, env):
    """Bound both pipes while draining them, with no shell and no retry."""
    try:
        proc = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=os.name != 'nt')
    except OSError as exc:
        raise WorkspaceError('Cannot start bd: ' + str(exc), 503, 'beads_unavailable') from None
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()

    def kill():
        try:
            if os.name != 'nt':
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass

    def drain(pipe, output):
        with pipe:
            while chunk := pipe.read(65536):
                if len(output) + len(chunk) > MAX_OUTPUT:
                    overflow.set(); kill(); break
                output.extend(chunk)

    readers = [threading.Thread(target=drain, args=(pipe, output), daemon=True)
               for pipe, output in zip((proc.stdout, proc.stderr), buffers)]
    for reader in readers:
        reader.start()
    try:
        proc.wait(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        kill(); proc.wait()
        raise WorkspaceError('Beads timed out. Check native bd before retrying a write.', 408, 'beads_timeout') from None
    finally:
        for reader in readers:
            reader.join(2)
    if overflow.is_set() or any(reader.is_alive() for reader in readers):
        kill()
        raise WorkspaceError('Beads output exceeded the panel limit; use native bd', 413, 'beads_output_limit')
    stdout, stderr = (bytes(value).decode('utf-8', errors='replace') for value in buffers)
    if proc.returncode:
        raise WorkspaceError((stderr or stdout or 'Beads command failed')[-3000:], 409, 'beads_command_failed')
    return stdout


class Client:
    def __init__(self, root):
        self.root = Path(root)
        self.exe = shutil.which(os.environ.get('EASYREAD_BEADS_COMMAND', 'bd'))
        if not self.exe:
            raise WorkspaceError('Install bd on the EasyRead server to manage Beads tasks', 503, 'beads_unavailable')
        if not (self.root / '.beads').exists():
            raise WorkspaceError('This project has no initialized Beads database. Initialize it explicitly with native bd.', 409, 'beads_uninitialized')
        self.exe = str(Path(self.exe).resolve())
        self.beads = validate(self.root)

    def run(self, args, *, write=False):
        # Recheck immediately before every command, including multi-command actions.
        validate(self.root)
        with tempfile.TemporaryDirectory(prefix='easyread-bd-') as temp:
            env = environment(self.root, Path(temp))
            version = supported_version(self.exe, Path(temp), env, execute, fresh=write)
            if write:
                marker = self.beads / '.local_version'
                if not marker.exists() or files.read_bytes(marker, 100).decode().strip() != version:
                    raise WorkspaceError('Beads version changed. Back up and upgrade this project explicitly with native bd before editing.', 409, 'beads_version')
            command = [self.exe, '--db=' + str(self.beads / 'dolt'), '--sandbox', '--json', '--actor=EasyRead']
            if not write:
                command.append('--readonly')
            output = execute(command + args, self.root, env)
        try:
            data = json.loads(output)
        except ValueError:
            raise WorkspaceError('Invalid JSON from bd; check native bd before retrying a write', 502, 'beads_invalid_output') from None
        if isinstance(data, dict) and 'ok' in data and 'data' in data:
            if not data['ok']:
                raise WorkspaceError('Beads operation failed', 409, 'beads_command_failed')
            data = data['data']
        return data

#!/usr/bin/env python3
"""Run on the remote host: --workspace /absolute/root --engine codex.

Checks prerequisites and temporary-file writability only; never installs tools
or creates projects. Use the same Python environment as the configured worker.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from easyread.workspaces.remote_worker import main

if __name__ == '__main__':
    raise SystemExit(main(['--check', *sys.argv[1:]]))

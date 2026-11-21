#!/usr/bin/env python3
"""Print the reviewed remote setup script; no installation or server mutation.

From this checkout: python scripts/remote_workspace_setup.py > remote-setup.sh
Review it, then run on the intended remote host with sh remote-setup.sh.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from easyread.workspaces.remote_setup import main

if __name__ == '__main__':
    main()

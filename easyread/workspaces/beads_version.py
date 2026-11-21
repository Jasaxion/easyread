"""Bounded version-probe cache for reads only; writes always recheck the CLI."""
from collections import OrderedDict
from pathlib import Path
import re
import threading
import time

from . import WorkspaceError

_TTL = 60
_LIMIT = 8
_CACHE = OrderedDict()
_LOCK = threading.Lock()


def supported_version(exe, cwd, env, execute, *, fresh=False):
    try:
        st = Path(exe).stat()
        identity = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    except OSError:
        identity = None  # Let the normal transport report an unavailable executable.
    with _LOCK:
        cached = _CACHE.get(exe)
        if not fresh and identity is not None and cached and cached[0] == identity and cached[2] > time.monotonic():
            _CACHE.move_to_end(exe)
            return cached[1]
        _CACHE.pop(exe, None)
    output = execute([exe, '--version'], cwd, env).strip()
    match = re.search(r'^bd version (1\.3\.[1-4])(?:\s|$)', output)
    if not match:
        raise WorkspaceError('The Beads panel supports bd 1.3.1–1.3.4; use the pinned official 1.3.4 release', 409, 'beads_version')
    version = match[1]
    if identity is not None:
        with _LOCK:
            _CACHE[exe] = (identity, version, time.monotonic() + _TTL)
            _CACHE.move_to_end(exe)
            while len(_CACHE) > _LIMIT:
                _CACHE.popitem(last=False)
    return version

"""Stdlib-only source sent to the configured Python before importing EasyRead.

It cannot be launched with ``-m``: the remote installation may be absent. Only
fixed codes cross this boundary; import errors can contain paths or credentials.
The worker still consumes the bearer/protocol handshake from stdin itself.
"""

SOURCE = '''import importlib.util, json, sys

def fail(code):
    print("EASYREAD_REMOTE " + json.dumps({"code": code}), flush=True)
    raise SystemExit(2)

if sys.version_info < (3, 10):
    fail("remote_python")
try:
    spec = importlib.util.find_spec("easyread.workspaces.remote_worker")
except (ImportError, AttributeError):
    fail("remote_worker_missing")
if spec is None:
    fail("remote_worker_missing")
try:
    for name in ("filelock", "mcp", "PIL"):
        if importlib.util.find_spec(name) is None:
            fail("remote_dependencies")
    from easyread.workspaces import remote_worker, remote_transport
    if str(remote_transport.PROTOCOL) != sys.argv[1] or remote_transport.build_id() != sys.argv[2]:
        fail("remote_protocol")
    sys.argv = [sys.argv[0]] + sys.argv[3:]
    result = remote_worker.main()
except ImportError:
    fail("remote_dependencies")
except (SyntaxError, AttributeError):
    fail("remote_protocol")
except Exception:
    fail("remote_worker_failed")
sys.exit(result)
'''

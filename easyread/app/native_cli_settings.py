"""Read native CLI settings without rewriting them or returning secrets to the UI."""
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib


def path(engine):
    if engine == "claude":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"
    if engine == "codex":
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    if engine == "pi":
        return Path(os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent").expanduser() / "settings.json"
    raise ValueError("Unsupported CLI")


def read(engine):
    source = path(engine)
    try:
        if engine == "pi":
            import stat
            if not stat.S_ISREG(source.stat().st_mode):
                raise ValueError("Pi settings must be a regular file")
            with source.open(encoding="utf-8-sig") as stream:
                raw = stream.read(1024 * 1024 + 1)
        else:
            raw = source.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    if len(raw) > 1024 * 1024:
        raise ValueError("Native CLI settings exceed size limit")
    value = json.loads(raw) if engine in ("claude", "pi") else tomllib.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid native CLI settings")
    return value


def codex_effective(cwd, executable="codex"):
    """Resolve native user/profile/project layers through the CLI, without a model turn.

    Used only when custom Q&A needs to disable inherited MCP servers. Never
    print/store the returned config: it can contain credentials.
    """
    process = subprocess.Popen([executable, "--cd", str(cwd), "app-server"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                               encoding="utf-8", errors="replace")
    messages = queue.Queue()
    def read_messages():
        for line in process.stdout:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            messages.put(value)
        messages.put(None)
    threading.Thread(target=read_messages, daemon=True).start()
    def send(value):
        process.stdin.write(json.dumps(value) + "\n")
        process.stdin.flush()
    try:
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "easyread-capability-config", "version": "1"}}})
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                response = messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if response is None:
                break
            if response.get("id") == 1:
                if "error" in response:
                    break
                send({"method": "initialized"})
                send({"id": 2, "method": "config/read", "params": {"includeLayers": False, "cwd": str(Path(cwd).resolve())}})
            elif response.get("id") == 2:
                result = response.get("result", {}).get("config")
                if isinstance(result, dict):
                    return result
                break
        raise ValueError("Cannot resolve effective Codex settings; custom MCP-off mode requires config/read support")
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=3)
        process.stdin.close(); process.stdout.close()


def summary(engine):
    source = path(engine)
    return {"path": str(source), "exists": source.is_file(), "modified": source.stat().st_mtime if source.is_file() else None}

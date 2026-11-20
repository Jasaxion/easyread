"""Native Codex / Claude Code CLI adapter for research-workspace sessions.

Each project keeps its own CLI home under the project metadata directory
(``CODEX_HOME`` / ``CLAUDE_CONFIG_DIR``), so native conversation state belongs to
the project and can be exported. Host authentication, the user's global config,
skills and plugins are inherited at launch and never exported. Mutable config
and credentials are private copies, never written back to the host.

Verified against codex-cli 0.169.0 and Claude Code 2.1.309:
- ``codex exec --json`` emits ``{"type":"thread.started","thread_id":...}`` first,
  and persists ``$CODEX_HOME/sessions/<y>/<m>/<d>/rollout-<ts>-<id>.jsonl``.
  ``codex exec resume <id>`` replays a relocated rollout file (new id, new cwd)
  without any index or sqlite state.
- ``claude -p --session-id <uuid>`` honours a caller-chosen id and writes
  ``$CLAUDE_CONFIG_DIR/projects/<slug>/<uuid>.jsonl`` where ``slug`` replaces
  every non-alphanumeric character of the cwd with ``-``. ``--resume <uuid>``
  replays a relocated transcript and keeps the earlier turns in context.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..app.i18n import tr

ENGINES = ("codex", "claude")
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MAX_TRANSCRIPT = 64 * 1024 * 1024
_NO_WINDOW = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

# Host capabilities inherited on launch. Mutable files are copied, never linked
# or synchronized back; only capability directories use inheritance links.
SPECS = {
    "codex": {
        "name": "Codex CLI", "command": "codex", "home_env": "CODEX_HOME", "host_default": ".codex",
        "links": ("auth.json", "config.toml", "hooks.json", "installation_id", "AGENTS.md",
                  "skills", "rules", "plugins", "packages", "prompts"),
    },
    "claude": {
        "name": "Claude Code", "command": "claude", "home_env": "CLAUDE_CONFIG_DIR", "host_default": ".claude",
        "links": (".claude.json", ".credentials.json", "settings.json", "settings.local.json", "CLAUDE.md",
                  "skills", "plugins", "hooks", "agents", "commands", "output-styles", "keybindings.json"),
    },
}


class SessionError(ValueError):
    """Carries an API code/status so the HTTP adapter can answer 400/404/409."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status

    def body(self):
        return {"error": str(self), "code": self.code}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def uuid7() -> str:
    """Codex ids are time-ordered UUIDv7; keep generated ids in the same shape."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms << 80) | (0x7 << 76) | (((rand >> 62) & 0xfff) << 64) | (0x2 << 62) | (rand & ((1 << 62) - 1))
    return str(uuid.UUID(int=value & ((1 << 128) - 1)))


def new_native_id(engine: str) -> str:
    return uuid7() if engine == "codex" else str(uuid.uuid4())


def valid_native_id(value) -> bool:
    return isinstance(value, str) and bool(UUID.match(value))


def executable(engine: str) -> str | None:
    override = os.environ.get(f"EASYREAD_WORKSPACE_{engine.upper()}_COMMAND")
    return shutil.which(override or SPECS[engine]["command"])


_versions: dict = {}
_versions_lock = threading.Lock()


def version(engine: str, ttl: float = 300.0) -> str:
    exe = executable(engine)
    if not exe:
        return ""
    with _versions_lock:
        hit = _versions.get(engine)
        if hit and time.monotonic() - hit[0] < ttl and hit[1] == exe:
            return hit[2]
    try:
        done = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10,
                              creationflags=_NO_WINDOW)
        raw = (done.stdout or done.stderr or "").strip()
        text = raw.splitlines()[0][:100] if raw else ""
    except (OSError, IndexError, subprocess.SubprocessError):
        text = ""
    with _versions_lock:
        _versions[engine] = (time.monotonic(), exe, text)
    return text


def engine_status(engine: str, terminal: bool) -> dict:
    exe = executable(engine)
    return {"id": engine, "name": SPECS[engine]["name"], "installed": bool(exe), "version": version(engine),
            "command": exe or "", "capabilities": {"resume": bool(exe), "terminal": bool(exe) and terminal,
                                                   "mcp": bool(exe), "skills": bool(exe)}}


# ---------- per-project CLI home ----------

def isolation_enabled() -> bool:
    return os.environ.get("EASYREAD_WORKSPACE_CLI_ISOLATION", "1") != "0"


def host_home(engine: str) -> Path:
    spec = SPECS[engine]
    return Path(os.environ.get(spec["home_env"]) or Path.home() / spec["host_default"])


def project_home(meta: Path, engine: str) -> Path:
    return Path(meta) / "sessions" / "native" / engine


def state_home(meta: Path, engine: str, isolated: bool | None = None) -> Path:
    """Resolve state without preparing auth/config (safe for export and import)."""
    private = isolation_enabled() if isolated is None else isolated
    return project_home(meta, engine) if private else host_home(engine)


def home(meta: Path, engine: str, isolated: bool | None = None) -> tuple[Path, bool]:
    private = isolation_enabled() if isolated is None else isolated
    root = state_home(meta, engine, private)
    if not private:
        return root, False
    try:
        for parent in (root, *root.parents):
            if parent.is_symlink():
                raise OSError("Private CLI home must not contain symbolic links")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        failed = refresh_links(engine, root)
        if failed:
            raise OSError("Cannot inherit native CLI configuration: " + ", ".join(failed))
    except (OSError, NotImplementedError) as error:
        raise SessionError("cli_home_unavailable", str(error), 409) from None
    return root, True


def refresh_links(engine: str, root: Path) -> list[str]:
    """Snapshot mutable files; link capability directories. Never sync to the host.

    Refresh a copy only when the host changes, preserving token refreshes made by
    the private CLI in the meantime. The fingerprint file contains no secrets.
    """
    import hashlib
    host, failed = host_home(engine), []
    stamp = root / ".inheritance.json"
    try:
        previous = json.loads(stamp.read_text("utf-8")) if stamp.is_file() and not stamp.is_symlink() else {}
    except (OSError, ValueError):
        previous = {}
    current = {}
    for name in SPECS[engine]["links"]:
        source, target = host / name, root / name
        if engine == "claude" and name == ".claude.json" and not source.exists():
            source = host.parent / ".claude.json"
        try:
            if not source.exists():
                # Remove old inheritance links, not private files created by the CLI.
                if target.is_symlink():
                    target.unlink()
                continue
            if source.is_dir():
                if target.is_symlink():
                    if target.resolve() == source.resolve():
                        continue
                    target.unlink()
                if not target.exists():
                    target.symlink_to(source.resolve(), target_is_directory=True)
                continue
            raw = source.read_bytes()
            if engine == "claude" and name == ".claude.json":
                config = json.loads(raw)
                # Never inherit other projects' trust, history or mutable settings.
                raw = json.dumps({key: config[key] for key in
                                  ("mcpServers", "oauthAccount", "hasCompletedOnboarding")
                                  if key in config}, ensure_ascii=False).encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            current[name] = digest
            if target.is_symlink():
                target.unlink()
            elif target.exists() and previous.get(name) == digest:
                continue
            tmp = target.with_name(target.name + ".inherit-tmp")
            tmp.unlink(missing_ok=True)
            with tmp.open("xb") as stream:
                os.chmod(tmp, 0o600)
                stream.write(raw)
            os.replace(tmp, target)
        except (OSError, ValueError, NotImplementedError):
            failed.append(name)
    if not failed:
        stamp.unlink(missing_ok=True)
        stamp.write_text(json.dumps(current), encoding="utf-8")
    return failed


def launch_env(meta: Path, engine: str, isolated: bool | None = None) -> tuple[dict, Path, bool]:
    from ..engines import netcheck
    cli_home, isolated = home(meta, engine, isolated)
    env = dict(netcheck.proxy_env() or os.environ)
    env[SPECS[engine]["home_env"]] = str(cli_home)
    root = Path(__file__).resolve().parents[2]
    if (root / "easyread" / "__init__.py").exists():
        previous = env.get("PYTHONPATH") or ""
        env["PYTHONPATH"] = os.pathsep.join([str(root)] + [p for p in previous.split(os.pathsep) if p and p != str(root)])
    env.pop("EASYREAD_TERMINAL", None)
    return env, cli_home, isolated


# ---------- project MCP injection ----------

MCP_NAME = "easyread_project"


def mcp_command() -> list[str]:
    override = os.environ.get("EASYREAD_WORKSPACE_MCP_COMMAND")
    if override:
        return shlex.split(override)
    if getattr(sys, "frozen", False):
        return [sys.executable, "--workspace-mcp"]
    return [sys.executable, "-m", "easyread.workspaces.mcp"]


def mcp_args(libroot: Path, pid: str) -> list[str]:
    return ["--library", str(libroot), "--project", pid]


def _toml(value) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------- argv ----------

def argv(engine: str, *, exe: str, prompt_stdin: bool, native_id: str | None, resume: bool,
         model: str = "", libroot: Path | None = None, pid: str = "", root: Path | None = None,
         last_message: Path | None = None) -> list[str]:
    """Headless turn argv. Full native permissions, tools, MCP and skills stay on."""
    if engine == "codex":
        args = [exe, "exec"]
        if resume:
            args += ["resume", str(native_id)]
        else:
            args += ["--color", "never"]
            if root is not None:
                args += ["--cd", str(root)]
        args += ["--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox"]
        if last_message is not None:
            args += ["-o", str(last_message)]
    else:
        args = [exe, "-p", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "bypassPermissions"]
        args += ["--resume", str(native_id)] if resume else ["--session-id", str(native_id)]
    if model:
        args += ["--model", model]
    args += project_mcp_args(engine, libroot, pid)
    return args + (["-"] if engine == "codex" and prompt_stdin else [])


def project_mcp_args(engine: str, libroot: Path | None, pid: str) -> list[str]:
    if libroot is None:
        return []
    command = mcp_command()
    if engine == "codex":
        return ["-c", f"mcp_servers.{MCP_NAME}.command=" + _toml(command[0]),
                "-c", f"mcp_servers.{MCP_NAME}.args=" + _toml(command[1:] + mcp_args(libroot, pid)),
                "-c", f"mcp_servers.{MCP_NAME}.enabled=true",
                "-c", f"mcp_servers.{MCP_NAME}.env_vars=" + _toml(["PYTHONPATH"]),
                "-c", f"mcp_servers.{MCP_NAME}.startup_timeout_sec=30"]
    # Additive: never --strict-mcp-config or disable native tools/skills.
    return ["--mcp-config", json.dumps({"mcpServers": {MCP_NAME: {
        "command": command[0], "args": command[1:] + mcp_args(libroot, pid)}}}, ensure_ascii=False)]


def terminal_argv(engine: str, native_id: str | None, model: str = "", *,
                  resume: bool | None = None, libroot: Path | None = None, pid: str = "") -> list[str]:
    exe = executable(engine)
    if not exe:
        raise SessionError("engine_missing", tr("找不到 {name} 命令，请先安装并登录", name=SPECS[engine]["name"]), 409)
    resume = bool(native_id) if resume is None else resume
    if engine == "codex":
        args = [exe] + (["resume", str(native_id)] if resume else [])
        args += ["--dangerously-bypass-approvals-and-sandbox"]
    else:
        args = [exe, "--permission-mode", "bypassPermissions"]
        if native_id:
            args += ["--resume" if resume else "--session-id", str(native_id)]
    return args + (["--model", model] if model else []) + project_mcp_args(engine, libroot, pid)


# ---------- event stream ----------

def _text(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) if value is not None else ""
    return text


class Stream:
    """Normalises native JSONL events into UI events and a final answer."""

    def __init__(self, engine: str):
        self.engine = engine
        self.native_id = ""
        self.text = ""
        self.usage: dict = {}
        self.error = ""
        self.finished = False
        self.model = ""
        self.notices: list[str] = []
        self._seen: set = set()

    def feed(self, line: str) -> list[dict]:
        line = (line or "").strip()
        if not line.startswith("{"):
            return []
        try:
            event = json.loads(line)
        except ValueError:
            return []
        if not isinstance(event, dict):
            return []
        items = self._claude(event) if self.engine == "claude" else self._codex(event)
        at = now_iso()
        return [{"at": at, **item} for item in items]

    # -- claude --
    def _claude(self, event: dict) -> list[dict]:
        kind, subtype = event.get("type"), event.get("subtype")
        if valid_native_id(event.get("session_id")):
            self.native_id = event["session_id"]
        if kind == "system" and subtype == "init":
            self.model = str(event.get("model") or "")
            return [{"kind": "init", "model": self.model, "tools": event.get("tools") or [],
                     "mcp_servers": [s.get("name") for s in event.get("mcp_servers") or [] if isinstance(s, dict)],
                     "skills": (event.get("skills") or [])[:40],
                     "permission_mode": event.get("permissionMode") or ""}]
        if kind == "system":
            text = _text(event.get("error") or subtype or "")
            self.notices.append(text)
            return [{"kind": "notice", "text": text, "subtype": subtype or ""}]
        if kind == "assistant":
            return self._blocks((event.get("message") or {}).get("content"))
        if kind == "user":
            return self._results((event.get("message") or {}).get("content"))
        if kind == "result":
            self.finished = True
            self.usage = _usage_claude(event)
            failed = bool(event.get("is_error")) or (subtype or "success") != "success"
            text = str(event.get("result") or "")
            if failed:
                self.error = _text(text or subtype or "") or tr("原生 CLI 未返回结果")
            elif text:
                self.text = text
            return [{"kind": "result", "ok": not failed, "text": _text(text),
                     "usage": self.usage, "duration_ms": event.get("duration_ms")}]
        return []

    def _blocks(self, content) -> list[dict]:
        out = []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and block.get("text"):
                self.text = block["text"]
                out.append({"kind": "text", "text": _text(block["text"])})
            elif btype == "thinking" and block.get("thinking"):
                out.append({"kind": "thinking", "text": _text(block["thinking"])})
            elif btype == "tool_use":
                out.append({"kind": "tool", "id": str(block.get("id") or ""), "name": str(block.get("name") or "tool"),
                            "detail": _text(block.get("input"))})
        return out

    def _results(self, content) -> list[dict]:
        out = []
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                out.append({"kind": "tool_result", "id": str(block.get("tool_use_id") or ""),
                            "ok": not block.get("is_error"), "text": _text(block.get("content"))})
        return out

    # -- codex --
    def _codex(self, event: dict) -> list[dict]:
        kind = event.get("type")
        if kind == "thread.started":
            if valid_native_id(event.get("thread_id")):
                self.native_id = event["thread_id"]
            return [{"kind": "init", "session_id": self.native_id}]
        if kind == "turn.started":
            return [{"kind": "turn"}]
        if kind in ("item.started", "item.updated", "item.completed"):
            return self._codex_item(event, kind)
        if kind == "turn.completed":
            self.finished = True
            self.usage = _usage_codex(event.get("usage"))
            return [{"kind": "result", "ok": True, "text": _text(self.text), "usage": self.usage}]
        if kind == "turn.failed":
            self.finished = True
            error = event.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            self.error = _text(message or tr("原生 CLI 未返回结果"))
            return [{"kind": "result", "ok": False, "text": self.error}]
        if kind == "error":
            text = _text(event.get("message") or "")
            self.notices.append(text)
            return [{"kind": "notice", "text": text, "subtype": "error"}]
        return []

    def _codex_item(self, event: dict, phase: str) -> list[dict]:
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        itype = item.get("item_type") or item.get("type") or ""
        iid = str(item.get("id") or "")
        if itype in ("agent_message", "assistant_message"):
            if phase != "item.completed":
                return []
            text = item.get("text") or item.get("message") or ""
            if text:
                self.text = str(text)
            return [{"kind": "text", "text": _text(text)}]
        if itype == "reasoning":
            if phase != "item.completed":
                return []
            text = item.get("text") or item.get("summary") or ""
            return [{"kind": "thinking", "text": _text(text)}] if text else []
        if itype == "todo_list":
            return [] if phase == "item.started" else [{"kind": "plan", "detail": _text(item.get("items"))}]
        name, detail = _codex_tool(itype, item)
        if phase == "item.started":
            if iid and iid in self._seen:
                return []
            self._seen.add(iid)
            return [{"kind": "tool", "id": iid, "name": name, "detail": detail}]
        if phase == "item.completed":
            ok = item.get("status") not in ("failed", "error") and item.get("exit_code") in (None, 0)
            return [{"kind": "tool_result", "id": iid, "ok": ok, "name": name,
                     "text": _text(item.get("aggregated_output") or item.get("output") or item.get("status") or "")}]
        return []


def _codex_tool(itype: str, item: dict) -> tuple[str, str]:
    if itype == "command_execution":
        return "shell", _text(item.get("command"))
    if itype in ("file_change", "patch_apply"):
        return "apply_patch", _text(item.get("changes") or item.get("files"))
    if itype == "mcp_tool_call":
        return f"{item.get('server') or 'mcp'}.{item.get('tool') or ''}", _text(item.get("arguments"))
    if itype == "web_search":
        return "web_search", _text(item.get("query"))
    return itype or "tool", _text({k: v for k, v in item.items() if k not in ("id", "item_type", "type")})


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _usage_claude(event: dict) -> dict:
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
    cached = _int(usage.get("cache_read_input_tokens"))
    cost = event.get("total_cost_usd")
    return {"input": _int(usage.get("input_tokens")) + cached + _int(usage.get("cache_creation_input_tokens")),
            "cached": cached, "output": _int(usage.get("output_tokens")),
            "cost_usd": cost if isinstance(cost, (int, float)) else None}


def _usage_codex(usage) -> dict:
    usage = usage if isinstance(usage, dict) else {}
    return {"input": _int(usage.get("input_tokens")), "cached": _int(usage.get("cached_input_tokens")),
            "output": _int(usage.get("output_tokens")), "cost_usd": None}


# ---------- native transcripts ----------

def claude_slug(cwd) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd)))


def transcript(engine: str, cli_home: Path, native_id: str, cwd) -> Path | None:
    if not valid_native_id(native_id):
        return None
    if engine == "claude":
        hits = [Path(cli_home) / "projects" / claude_slug(cwd) / f"{native_id}.jsonl"]
    else:
        hits = list((Path(cli_home) / "sessions").glob(f"**/rollout-*-{native_id}.jsonl"))
    matches = []
    for path in hits:
        if not path.exists() and not path.is_symlink():
            continue
        records = validate_transcript(engine, read_transcript(path), native_id)
        if Path(_old_cwd(records)).resolve() == Path(cwd).resolve():
            matches.append(path)
    if len(matches) > 1:
        raise SessionError("native_conflict", "Multiple native transcripts use the same session ID", 409)
    return matches[0] if matches else None


def project_transcripts(engine: str, cli_home: Path, cwd) -> dict[str, Path]:
    """Project-owned, top-level sessions only, for fresh TUI identity discovery."""
    base = Path(cli_home) / ("projects" if engine == "claude" else "sessions")
    hits = ((base / claude_slug(cwd)).glob("*.jsonl") if engine == "claude"
            else base.glob("**/rollout-*.jsonl"))
    found = {}
    for path in hits:
        # A TUI may be midway through its first write; inspect only complete lines.
        text = read_transcript(path)
        complete = text[:text.rfind("\n") + 1]
        if not complete:
            continue
        records = _records(complete)
        if engine == "codex":
            meta = next((r.get("payload", {}) for r in records if r.get("type") == "session_meta"), {})
            if isinstance(meta.get("source"), dict):  # subagent sessions are not a TUI
                continue
            native_id = meta.get("id") or meta.get("session_id")
        else:
            native_id = next((r.get("sessionId") for r in records if r.get("sessionId")), "")
        if valid_native_id(native_id) and _old_cwd(records) and Path(_old_cwd(records)).resolve() == Path(cwd).resolve():
            validate_transcript(engine, complete, native_id)
            found[native_id] = path
    return found


def transcript_target(engine: str, cli_home: Path, native_id: str, cwd) -> Path:
    if engine == "claude":
        return Path(cli_home) / "projects" / claude_slug(cwd) / f"{native_id}.jsonl"
    stamp = datetime.now(timezone.utc)
    return (Path(cli_home) / "sessions" / stamp.strftime("%Y/%m/%d") /
            f"rollout-{stamp.strftime('%Y-%m-%dT%H-%M-%S')}-{native_id}.jsonl")


def _records(text: str) -> list[dict]:
    out = []
    try:
        for line in text.splitlines():
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict) or not isinstance(value.get("type"), str):
                    raise ValueError()
                out.append(value)
    except ValueError:
        raise SessionError("invalid_transcript", "Malformed native transcript") from None
    return out


def validate_transcript(engine: str, text: str, native_id: str | None = None) -> list[dict]:
    records = _records(text)
    if engine == "codex":
        metas = [r.get("payload") for r in records if r.get("type") == "session_meta"]
        if len(metas) != 1 or not isinstance(metas[0], dict):
            raise SessionError("invalid_transcript", "Missing native session metadata")
        ids = [metas[0][key] for key in ("id", "session_id") if key in metas[0]]
        if any(not isinstance(r.get("payload"), dict) for r in records):
            raise SessionError("invalid_transcript", "Invalid native record payload")
        if any(r.get("type") in ("user", "assistant") for r in records):
            raise SessionError("invalid_transcript", "Wrong native transcript engine")
    else:
        if any(r.get("type") == "session_meta" for r in records):
            raise SessionError("invalid_transcript", "Wrong native transcript engine")
        ids = [r["sessionId"] for r in records if "sessionId" in r]
        if not any(r.get("type") in ("user", "assistant") for r in records):
            raise SessionError("invalid_transcript", "Missing native conversation records")
        for record in records:
            if record.get("type") in ("user", "assistant"):
                message = record.get("message")
                if not isinstance(message, dict) or not isinstance(message.get("content"), (str, list)):
                    raise SessionError("invalid_transcript", "Invalid native conversation message")
    if (not ids or any(not valid_native_id(i) for i in ids) or len(set(ids)) != 1
            or (native_id is not None and ids[0] != native_id) or not _old_cwd(records)):
        raise SessionError("invalid_transcript", "Invalid native session ID or working directory")
    return records


def transcript_messages(engine: str, text: str) -> list[dict]:
    """Read actual CLI conversation records, excluding instructions and tool chatter."""
    complete = text[:text.rfind("\n") + 1]
    records = _records(complete)
    out = []
    for r in records:
        role, content = "", ""
        if engine == "codex":
            p = r.get("payload") or {}
            if r.get("type") == "event_msg" and p.get("type") in ("user_message", "agent_message"):
                role = "user" if p["type"] == "user_message" else "assistant"
                content = p.get("message") or ""
            elif r.get("type") == "event_msg" and p.get("type") == "item_completed":
                item = p.get("item") or {}
                kind = item.get("type")
                if kind in ("user_message", "agent_message", "UserMessage", "AgentMessage"):
                    role = "user" if kind in ("user_message", "UserMessage") else "assistant"
                    blocks = item.get("content") or []
                    content = blocks if isinstance(blocks, str) else "\n".join(
                        b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("text"))
        elif r.get("type") in ("user", "assistant") and not r.get("isSidechain"):
            role = r["type"]
            blocks = (r.get("message") or {}).get("content", "")
            content = blocks if isinstance(blocks, str) else "\n".join(
                b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        if role and isinstance(content, str) and content:
            out.append({"role": role, "content": content, "at": r.get("timestamp") or now_iso()})
    return out


def _old_cwd(records: list[dict]) -> str:
    for record in records:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
        value = payload.get("cwd")
        if isinstance(value, str) and value:
            return value
    return ""


def _repath(value: str, old: str, new: str) -> str:
    if not old or value == old:
        return new
    base = old.rstrip("/")
    return new.rstrip("/") + value[len(base):] if value.startswith(base + "/") else value


def _retarget(value, old: str, new: str):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "cwd" and isinstance(item, str):
                out[key] = _repath(item, old, new)
            elif key == "runtime_workspace_roots" and isinstance(item, list):
                out[key] = [_repath(x, old, new) if isinstance(x, str) else x for x in item]
            else:
                out[key] = _retarget(item, old, new)
        return out
    return [_retarget(x, old, new) for x in value] if isinstance(value, list) else value


def relocate(engine: str, text: str, native_id: str, cwd) -> str:
    """Rewrite a transcript for a new project: new native id and new workspace path."""
    if not valid_native_id(native_id):
        raise SessionError("invalid_transcript", "Invalid target native session ID")
    records = validate_transcript(engine, text)
    if not records:
        raise SessionError("invalid_archive", tr("会话归档内容无效"))
    old, new = _old_cwd(records), str(Path(cwd))
    out = []
    for record in records:
        record = _retarget(record, old, new)
        if engine == "claude":
            if isinstance(record.get("sessionId"), str):
                record["sessionId"] = native_id
        else:
            payload = record.get("payload")
            if record.get("type") == "session_meta" and isinstance(payload, dict):
                for key in ("session_id", "id"):
                    if key in payload:
                        payload[key] = native_id
        out.append(json.dumps(record, ensure_ascii=False))
    return "\n".join(out) + "\n"


def read_transcript(path: Path) -> str:
    from .files import read_bytes
    return read_bytes(path, MAX_TRANSCRIPT).decode("utf-8")


def write_transcript(path: Path, text: str) -> None:
    from .files import directory
    path.parent.mkdir(parents=True, exist_ok=True)
    with directory(path.parent):
        if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
            raise SessionError("invalid_transcript", "Native transcript must be a regular private file")
        tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with tmp.open("x", encoding="utf-8") as stream:
                stream.write(text)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def stop_process(proc) -> None:
    """Stop the entire native process group, including a reaped leader's children."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    finally:
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        raise SessionError("conflict", "Native process has not exited", 409) from None

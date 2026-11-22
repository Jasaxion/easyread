"""Explicit, cached native command/Skill discovery (never ordinary polling)."""
from __future__ import annotations

from copy import deepcopy
import time

from . import session_cli as cli


def listing(owner, pid, sid):
    with owner.use(pid, sid) as runtime:
        if getattr(runtime, "engine", None) == "pi":
            from .pi_controls import catalog
            return {"commands": catalog(runtime), "controls": ["compact"], "adapter": "pi_rpc"}
        if "skills/list" not in runtime.methods:
            raise cli.SessionError("capability_unavailable", "Installed Codex does not expose native Skills", 409)
        cached = getattr(runtime, "commands_cache", None)
        if cached and time.monotonic() - cached[0] < 60:
            return deepcopy(cached[1])
        result = runtime.call("skills/list", {"cwds": [str(runtime.cwd)], "forceReload": False})
        entries = result.get("data", [])
        if not isinstance(entries, list) or len(entries) > 256:
            raise cli.SessionError("native_rpc_error", "Codex returned an invalid Skill inventory", 409)
        commands = []
        for entry in entries:
            for skill in entry.get("skills", []):
                if skill.get("enabled") and isinstance(skill.get("name"), str):
                    commands.append({"name": skill["name"][:160], "description": str(skill.get("description") or "")[:2000], "source": "skill"})
                if len(commands) > 2048:
                    raise cli.SessionError("native_rpc_error", "Codex Skill inventory exceeds the limit", 409)
        public = {"commands": commands, "controls": [], "adapter": "codex_app_server"}
        runtime.commands_cache = (time.monotonic(), public)
        return deepcopy(public)

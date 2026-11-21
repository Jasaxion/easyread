"""Public model candidates for workspace CLIs, without running model turns.

Explicit Codex catalogs are authoritative, including when unreadable. Claude
candidates are aliases/configuration/observations, not an availability guarantee.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import stat
import threading
import time

from ..app import cli_models, config, native_cli_settings
from ..chat import chat_models
from . import WorkspaceError, files, codex_model_list
from . import session_cli as cli, session_reasoning as reasoning, pi_sessions
from .sessions import Sessions

MAX_FILE = 8 * 1024 * 1024
CACHE_TTL = 60.0
REFRESH_COOLDOWN = 5.0
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_refresh_lock = threading.Lock()
# Display metadata only; URLs, paths and credential-like strings are not labels.
_PRIVATE = re.compile(r"(?:[a-z][a-z0-9+.-]*://|www\.|(?:^|\s)(?:[/~\\]|[A-Za-z]:\\)|"
                      r"\b(?:sk-|Bearer\s|api[_-]?key\s*[:=]|access[_-]?token\s*[:=]))", re.I)


def _text(value, limit=160) -> str:
    if (not isinstance(value, str) or _PRIVATE.search(value)
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        return ""
    return value.strip()[:limit]


def _model_id(value, engine=None) -> str:
    if not isinstance(value, str) or not _text(value, 121):
        return ""
    try:
        return Sessions._model(value.strip(), engine)
    except cli.SessionError:
        return ""


def _models(rows, source, engine=None) -> list[dict]:
    out, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        mid = _model_id(row.get("id"), engine)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        item = {"id": mid, "name": _text(row.get("name")) or mid, "source": source}
        description = _text(row.get("desc"), 400)
        if description:
            item["description"] = description
        if isinstance(row.get("reasoning_levels"), list):
            levels = list(dict.fromkeys(
                value for value in row["reasoning_levels"] if reasoning.valid(value, "codex")))
            # Missing usable metadata is unknown, not an inherit-only model.
            if levels:
                item["reasoning_levels"] = levels
        if reasoning.valid(row.get("default_reasoning"), "codex"):
            item["default_reasoning"] = row["default_reasoning"]
        out.append(item)
        if len(out) == codex_model_list.MAX_MODELS:
            break
    return out


def _read(path: Path, limit=MAX_FILE, *, private=False) -> bytes:
    if private:
        # Reject links in any private-home ancestor and pinned file itself.
        return files.read_bytes(path, limit=limit)
    # Host paths may intentionally be symlinks; still reject FIFOs/devices and
    # bound the actual read, rather than relying only on a pre-read stat.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Not a regular file")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("File limit")
    return data


def _settings(engine) -> tuple[dict, str]:
    try:
        raw = _read(native_cli_settings.path(engine), 1024 * 1024).decode("utf-8-sig")
        data = json.loads(raw) if engine == "claude" else native_cli_settings.tomllib.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Invalid settings")
        return data, ""
    except FileNotFoundError:
        return {}, ""
    except (OSError, ValueError, RuntimeError):
        return {}, "settings_unavailable"


def _catalog(path, source, *, private=False) -> list[dict]:
    try:
        data = json.loads(_read(path, private=private))
        return _models(cli_models.codex_catalog(data), source)
    except (OSError, ValueError, RuntimeError, WorkspaceError):
        return []


def _live(executable, *, refresh=False, cached_only=False) -> dict | None:
    home = cli.host_home("codex")
    key = (str(home.absolute()), executable)
    # Single flight also caches failures. A burst of refresh clicks cannot queue
    # a new process per HTTP request; deliberate later refreshes bypass the TTL.
    acquired = (_refresh_lock.acquire(blocking=False) if cached_only
                else _refresh_lock.acquire(timeout=codex_model_list.TIMEOUT + 3))
    if not acquired:
        return None if cached_only else {"models": [], "default": "", "warning": "refresh_busy"}
    try:
        now = time.monotonic()
        hit = _cache.get(key)
        if hit and now - hit[0] < (REFRESH_COOLDOWN if refresh else CACHE_TTL):
            return copy.deepcopy(hit[1])
        if cached_only:
            return None
        result = codex_model_list.query(executable, home)
        _cache[key] = (time.monotonic(), result)
        while len(_cache) > 8:
            _cache.pop(next(iter(_cache)))
        return copy.deepcopy(result)
    finally:
        _refresh_lock.release()


def _codex(meta, settings, executable, refresh) -> dict:
    default = _model_id(settings.get("model"))
    configured = settings.get("model_catalog_json", "")
    if configured != "":
        rows = []
        if isinstance(configured, str):
            try:
                path = Path(configured).expanduser()
                if not path.is_absolute():
                    path = cli.host_home("codex") / path
                rows = _catalog(path, "model_catalog_json")
            except (OSError, ValueError, RuntimeError):
                pass
        return {"default": default, "models": rows, "source": "model_catalog_json",
                **({} if rows else {"warning": "catalog_unavailable"})}
    rows = _catalog(cli.host_home("codex") / "models_cache.json", "models_cache")
    source = "models_cache"
    if not rows:
        source = "project_models_cache"
        rows = _catalog(cli.project_home(meta, "codex") / "models_cache.json", source, private=True)
    result = {"default": default, "models": rows, "source": source if rows else "configured"}
    if executable:
        live = _live(executable, refresh=refresh, cached_only=bool(rows) and not refresh)
        if live:
            live_rows = _models(live["models"], "model_list")
            if live_rows:
                result.update(models=live_rows, source="model_list", default=default or _model_id(live.get("default")))
            if live.get("warning") or not live_rows:
                result["warning"] = live.get("warning") or "model_list_unavailable"
    return result


def _claude(settings) -> dict:
    try:
        seen = json.loads(_read(chat_models._seen_path(), 1024 * 1024))
        if not isinstance(seen, dict):
            seen = {}
    except (OSError, ValueError, RuntimeError):
        seen = {}
    aliases = []
    for alias, name, _ in cli_models.CLAUDE_ALIASES:
        actual = _model_id(seen.get(alias))
        aliases.append({"id": alias, "name": name, "desc": chat_models.pretty(actual) if actual else ""})
    rows = _models(aliases, "claude_aliases")
    rows += _models([{"id": value, "name": chat_models.pretty(value)} for key, value in seen.items()
                     if isinstance(value, str) and (key in ("opus", "sonnet", "haiku", "_default", "last-default:claude")
                                                   or value.startswith("claude-"))], "observed")
    settings_env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
    keys = ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL")
    rows += _models([{"id": env.get(key)} for env in (os.environ, settings_env) for key in keys], "environment")
    rows += _models([{"id": settings.get("model")}], "configured_default")
    default = (_model_id(os.environ.get("ANTHROPIC_MODEL")) or _model_id(settings_env.get("ANTHROPIC_MODEL"))
               or _model_id(settings.get("model")) or _model_id(seen.get("_default")))
    return {"default": default, "models": rows, "source": "claude_aliases", "warning": "availability_unverified"}


def _pi(executable, cfg):
    # Share core's restricted, offline discovery; never launch inside a project.
    # Bound and sanitize the workspace's public display fields independently.
    from ..app import pi_models
    native = cfg.get("pi") if isinstance(cfg.get("pi"), dict) else {}
    discovered = pi_models.catalog(native)
    candidates = _models(discovered.get("models", []), "pi_catalog", "pi")
    native_by_id = {row["id"]: row for row in candidates}
    configured = _models([{"id": native.get("model")}] + [
        {"id": item.get("model"), "name": item.get("name")} for item in chat_models.models(cfg)
        if isinstance(item, dict) and item.get("engine") == "pi"], "easyread_config", "pi")
    rows, seen = [], set()
    for candidate in configured + candidates:
        row = native_by_id.get(candidate["id"], candidate)
        if row["id"] in seen:
            continue
        try:
            pi_sessions.model(row["id"])
        except cli.SessionError:
            continue
        if "reasoning_levels" in row:
            row["reasoning_levels"] = [value for value in row["reasoning_levels"] if reasoning.valid(value, "pi")]
            if not row["reasoning_levels"]:
                row.pop("reasoning_levels")
        if "default_reasoning" in row and not reasoning.valid(row["default_reasoning"], "pi"):
            row.pop("default_reasoning")
        rows.append(row)
        seen.add(row["id"])
        if len(rows) == codex_model_list.MAX_MODELS:
            break
    try:
        default = pi_sessions.model(_model_id(discovered.get("default"), "pi"))
    except cli.SessionError:
        default = ""
    if default and not any(row["id"] == default for row in rows):
        rows = [{"id": default, "name": default, "source": "configured_default"}, *rows][:codex_model_list.MAX_MODELS]
    result = {"engine": "pi", "models": rows, "default": default, "source": "pi_catalog",
              "installed": bool(executable), "reasoning_levels": list(reasoning.LEVELS["pi"]),
              "warning": "availability_unverified" if executable else "cli_missing"}
    if reasoning.valid(discovered.get("configured_reasoning"), "pi"):
        result["configured_reasoning"] = discovered["configured_reasoning"]
    return result


def listing(store, pid: str, engine: str, *, refresh=False, cfg=None) -> dict:
    """GET seam. Validates project identity but never prepares native session state."""
    store.root(pid)
    if engine not in cli.ENGINES:
        raise cli.SessionError("invalid_parameter", "Choose Codex, Claude Code or Pi Agent")
    executable = cli.executable(engine)
    if engine == "pi":
        return _pi(executable, config.load() if cfg is None else cfg)
    settings, warning = _settings(engine)
    if engine == "claude":
        result = _claude(settings)
    elif warning:
        # An invalid config may have an explicit catalog; do not silently mix in
        # an old cache or launch a CLI with configuration we could not read.
        result = {"default": "", "models": [], "source": "configured"}
    else:
        result = _codex(store.meta(pid), settings, executable, refresh)
    cfg = config.load() if cfg is None else cfg
    native = cfg.get(engine) if isinstance(cfg.get(engine), dict) else {}
    configured = _models([{"id": native.get("model")}] + [
        {"id": item.get("model"), "name": item.get("name")} for item in chat_models.models(cfg)
        if isinstance(item, dict) and item.get("engine") == engine], "easyread_config")
    # Reserve room for configured choices even when a native catalog has 200
    # entries. Keep richer native labels/source for IDs already in that catalog.
    rows = result["models"]
    native_by_id = {}
    for row in rows:
        native_by_id.setdefault(row["id"], row)
    prioritized = _models([{"id": result["default"]}], "configured_default") + configured
    output, ids = [], set()
    for row in prioritized + rows:
        if row["id"] not in ids:
            ids.add(row["id"])
            output.append(native_by_id.get(row["id"], row))
        if len(output) == codex_model_list.MAX_MODELS:
            break
    result.update(engine=engine, models=output, installed=bool(executable),
                  reasoning_levels=list(reasoning.LEVELS[engine]))
    settings_env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
    configured_effort = (settings.get("model_reasoning_effort") if engine == "codex" else
                         os.environ.get("CLAUDE_CODE_EFFORT_LEVEL") or settings_env.get("CLAUDE_CODE_EFFORT_LEVEL")
                         or settings.get("effortLevel"))
    if reasoning.valid(configured_effort, engine):
        result["configured_reasoning"] = configured_effort
    warning = warning or result.get("warning", "")
    if not executable and warning in ("", "availability_unverified"):
        warning = "cli_missing"
    if warning:
        result["warning"] = warning
    return result

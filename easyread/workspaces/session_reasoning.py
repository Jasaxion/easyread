"""Session-local effort choices and native CLI default inheritance on resume."""
from __future__ import annotations

import re

from . import session_cli as cli

# Recent Codex protocols accept model-advertised identifiers, not a closed enum.
# These are unverified fallback choices, never aliases across engines.
LEVELS = {
    "codex": ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"),
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "pi": ("off", "minimal", "low", "medium", "high", "xhigh", "max"),
}
CODEX_EFFORT = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def valid(value, engine):
    return isinstance(value, str) and (bool(CODEX_EFFORT.fullmatch(value)) if engine == "codex"
                                      else value in LEVELS[engine])


def effort(value, engine):
    if value == "":
        return ""
    if not valid(value, engine):
        raise cli.SessionError("invalid_parameter", "Unsupported reasoning_effort for " + engine)
    return value


def validate(store, pid, engine, model, value):
    value = effort(value, engine)
    if not value:
        return value
    from . import model_catalog
    catalog = model_catalog.listing(store, pid, engine)
    selected = model or catalog.get("default", "")
    row = next((row for row in catalog["models"] if row["id"] == selected), {})
    levels = row.get("reasoning_levels")
    if levels and value not in levels:
        raise cli.SessionError("invalid_parameter", "Unsupported reasoning_effort for the selected model")
    return value


def argv(engine, value):
    value = effort(value, engine)
    if not value:
        return []
    return (["-c", "model_reasoning_effort=" + cli._toml(value)] if engine == "codex"
            else ["--thinking" if engine == "pi" else "--effort", value])


def thread_config(value):
    value = effort(value, "codex")
    return {"config": {"model_reasoning_effort": value}} if value else {}


def codex_defaults(call, model, reasoning_effort):
    """Resume otherwise reuses saved turn settings, even in a fresh AppServer.

    Read actual native config/model metadata, never guess a default or write a
    shared config file. The session still persists empty strings for inheritance.
    """
    if model and reasoning_effort:
        return model, reasoning_effort
    try:
        return _codex_defaults(call, model, reasoning_effort)
    except (cli.SessionError, ValueError, TypeError, AttributeError):
        raise cli.SessionError("defaults_unavailable", "Cannot determine Codex CLI defaults; select an explicit model and reasoning effort", 409) from None


def _codex_defaults(call, model, reasoning_effort):
    config = call("config/read", {"includeLayers": False}).get("config") or {}
    selected = model or config.get("model") or ""
    value = reasoning_effort or config.get("model_reasoning_effort") or ""
    if selected and value:
        return _resolved(selected, value)
    rows, cursor, cursors = [], None, set()
    for _ in range(4):
        page = call("model/list", {"limit": 200 - len(rows), "cursor": cursor})
        data = page.get("data")
        if not isinstance(data, list):
            break
        rows.extend(row for row in data[:200 - len(rows)] if isinstance(row, dict))
        if not selected:
            selected = next((row.get("model") or row.get("id") for row in rows if row.get("isDefault") is True), "")
        row = next((row for row in rows if (row.get("model") or row.get("id")) == selected), {})
        resolved = value or row.get("defaultReasoningEffort")
        if selected and resolved:
            return _resolved(selected, resolved)
        cursor = page.get("nextCursor")
        if not isinstance(cursor, str) or not cursor or len(cursor) > 1024 or cursor in cursors or len(rows) >= 200:
            break
        cursors.add(cursor)
    raise cli.SessionError("defaults_unavailable", "Cannot determine Codex CLI defaults; select an explicit model and reasoning effort", 409)


def _resolved(model, value):
    from .sessions import Sessions
    try:
        return Sessions._model(model), effort(value, "codex")
    except cli.SessionError:
        raise cli.SessionError("defaults_unavailable", "Codex CLI defaults are invalid; select an explicit model and reasoning effort", 409) from None

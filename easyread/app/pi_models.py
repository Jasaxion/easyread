"""Non-inference Pi model discovery with no extensions, credentials output or logs."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from functools import lru_cache

from ..engines import pi_cli
from . import native_cli_settings

MAX_OUTPUT = 1024 * 1024
DISCOVERY_TIMEOUT = 8
_ID = re.compile(r"[A-Za-z0-9_@~][A-Za-z0-9_./:@~+%-]{0,239}\Z")
_TOKEN_COUNT = re.compile(r"\d+(?:\.\d+)?[KM]?\Z")
LIMITATIONS = ["Discovery excludes extension-provided and network-discovered models.",
               "Thinking levels are Pi levels; the selected model may clamp unsupported levels."]


def configured():
    try:
        settings = native_cli_settings.read("pi")
    except (OSError, ValueError):
        return {}, "invalid"
    return settings, "ok" if native_cli_settings.path("pi").is_file() else "missing"


def default_model():
    settings, _ = configured()
    provider, model = settings.get("defaultProvider"), settings.get("defaultModel")
    return provider + "/" + model if all(isinstance(s, str) and _ID.fullmatch(s) for s in (provider, model)) else ""


def parse_table(output):
    models = {}
    header = False
    for line in output.split("\n"):
        fields = line.split()
        if fields == ["provider", "model", "context", "max-out", "thinking", "images"]:
            header = True
            continue
        if not header or len(fields) != 6:
            continue
        provider, model, context, max_out, thinking, images = fields
        if (not all(_ID.fullmatch(s) for s in (provider, model))
                or not all(_TOKEN_COUNT.fullmatch(s) for s in (context, max_out))
                or thinking not in ("yes", "no") or images not in ("yes", "no")):
            continue
        identity = provider + "/" + model
        models[identity] = {"id": identity, "name": identity, "desc": "", "source": "native",
                            "reasoning_levels": list(pi_cli.THINKING_LEVELS) if thinking == "yes" else ["off"],
                            "default_reasoning": "", "vision": images == "yes"}
    return list(models.values())


@lru_cache(maxsize=8)
def _discover(executable, agent_dir, bucket):
    # File-backed output bounds memory even if an unexpected CLI emits endlessly.
    with tempfile.TemporaryDirectory(prefix="easyread-pi-models-") as directory, tempfile.TemporaryFile() as output:
        env = dict(os.environ, PI_CODING_AGENT_DIR=agent_dir, NO_COLOR="1", FORCE_COLOR="0", PI_OFFLINE="1")
        try:
            proc = subprocess.Popen([executable, *pi_cli.LEAN_ARGS, "--no-session", "--no-tools", "--list-models"],
                                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                                    cwd=directory, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            return "unavailable", ""
        try:
            deadline = time.monotonic() + DISCOVERY_TIMEOUT
            while proc.poll() is None:
                if os.fstat(output.fileno()).st_size > MAX_OUTPUT:
                    return "too_large", ""
                if time.monotonic() >= deadline:
                    return "timeout", ""
                time.sleep(.05)
            if proc.returncode:
                return "failed", ""
            output.seek(0)
            data = output.read(MAX_OUTPUT + 1)
            if len(data) > MAX_OUTPUT:
                return "too_large", ""
            return "ok", data.decode("utf-8", errors="replace")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def catalog(c):
    settings, config_status = configured()
    default = default_model()
    effort = settings.get("defaultThinkingLevel")
    effort = effort if effort in pi_cli.THINKING_LEVELS else ""
    executable = pi_cli.path(c)
    status, output = _discover(executable, str(native_cli_settings.path("pi").parent.absolute()), int(time.monotonic() // 60)) if executable else ("unavailable", "")
    models = parse_table(output)
    levels = settings.get("modelThinkingLevels")
    levels = levels if isinstance(levels, dict) else {}
    for model in models:
        level = levels.get(model["id"], effort)
        model["default_reasoning"] = level if level in pi_cli.THINKING_LEVELS else ""
    if default and not any(model["id"] == default for model in models):
        models.append({"id": default, "name": default, "desc": "", "source": "configured_default",
                       "reasoning_levels": list(pi_cli.THINKING_LEVELS),
                       "default_reasoning": levels.get(default) if levels.get(default) in pi_cli.THINKING_LEVELS else effort})
    return {"models": models, "default": default, "configured_reasoning": effort,
            "diagnostics": [{"source": "config", "status": config_status}, {"source": "list_models", "status": status}],
            "limitations": LIMITATIONS}

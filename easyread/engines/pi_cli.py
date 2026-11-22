"""Pi Agent one-shot JSON adapter. Tool restriction is not an OS sandbox."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
# Explicit prompts also suppress native SYSTEM.md and APPEND_SYSTEM.md discovery.
LEAN_ARGS = ["--no-approve", "--offline", "--no-extensions", "--no-mcp", "--no-skills",
             "--no-context-files", "--no-prompt-templates", "--no-themes",
             "--system-prompt", "You are EasyRead's reading assistant. Follow the supplied task using only available tools.",
             "--append-system-prompt", "Do not claim unavailable capabilities or actions you did not perform."]


def path(c):
    return shutil.which(c.get("command") or "pi")


def build_args(executable, c, images=(), skill_args=()):
    from .qa_capabilities import runtime_policy, validate
    selected = runtime_policy("pi", c["_qa_policy"]) if c.get("_qa_policy") else validate("pi", {})
    full = selected["mode"] == "full"
    extra = c.get("extra_args") or []
    if not isinstance(extra, list) or any(not isinstance(arg, str) for arg in extra):
        raise ValueError("Pi extra_args must be a list of strings")
    # Explicit resource arguments bypass Pi's --no-* flags. Never allow them in
    # lean/custom calls, and never retry with weaker restrictions on old CLIs.
    if not full and any(arg != "--verbose" for arg in extra):
        raise ValueError("Pi lean/custom mode supports only --verbose in extra_args; use native full Q&A for other options")
    if full and any(arg.split("=", 1)[0] in {
        "--mode", "--session", "--session-id", "--session-dir", "--continue", "-c", "--resume", "-r",
        "--fork", "--export", "--help", "-h", "--version", "-v", "--list-models", "--",
    } for arg in extra):
        raise ValueError("Pi extra_args cannot change the one-shot protocol or resume sessions")
    args = [executable, *extra, "--mode", "json", "--print", "--no-session"]
    if full:
        args += ["--approve"]  # This invocation only; does not persist project trust.
    else:
        args += LEAN_ARGS
        tools = selected["allowed_tools"] if c.get("_qa_policy") else ["read"]
        args += ["--tools", ",".join(tools)] if tools else ["--no-tools"]
    model = c.get("model") or ""
    if model:
        if not isinstance(model, str) or "/" not in model or not all(model.split("/", 1)):
            raise ValueError("Pi model must be a fully qualified provider/model ID")
        provider, model_id = model.split("/", 1)
        args += ["--provider", provider, "--model", model_id]
    effort = c.get("reasoning_effort") or ""
    if effort:
        if effort not in THINKING_LEVELS:
            raise ValueError("Invalid Pi thinking level")
        args += ["--thinking", effort]
    args += list(skill_args)
    # -- stops image paths from being parsed as options; prompt travels on stdin.
    args += ["--", *("@" + str(Path(image).resolve()) for image in images)]
    return args


def _usage(u):
    cached = int(u.get("cacheRead") or 0)
    used = {"input": int(u.get("input") or 0) + int(u.get("cacheWrite") or 0) + cached,
            "cached": cached, "output": int(u.get("output") or 0)}
    if isinstance(u.get("cost"), dict) and u["cost"].get("total") is not None:
        used["cost_usd"] = float(u["cost"]["total"])
    return used


def parse(out):
    """Read completed messages only, not duplicate turn/agent snapshots or deltas."""
    result = {"text": "", "error": "", "model": "", "aborted": False, "settled": False,
              "stop_reason": "", "usage": []}
    for line in out.split("\n"):  # U+2028/U+2029 are valid within JSON strings.
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "message_end":
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            if message.get("role") in ("assistant", "toolResult") and isinstance(message.get("usage"), dict):
                result["usage"].append(_usage(message["usage"]))
            if message.get("role") != "assistant":
                continue
            content = message.get("content") or []
            result["text"] = "\n\n".join(block["text"] for block in content
                                           if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)).strip()
            result["stop_reason"] = message.get("stopReason") or ""
            result["error"] = str(message.get("errorMessage") or "") if result["stop_reason"] in ("error", "aborted") else ""
            result["aborted"] = result["stop_reason"] == "aborted"
            if message.get("provider") and message.get("model"):
                result["model"] = str(message["provider"]) + "/" + str(message["model"])
        elif kind == "compaction_end" and isinstance(event.get("result"), dict):
            if isinstance(event["result"].get("usage"), dict):
                result["usage"].append(_usage(event["result"]["usage"]))
        elif kind == "auto_retry_end" and event.get("success") is False:
            result["error"] = str(event.get("finalError") or "Pi retry failed")
        elif kind == "agent_settled":
            result["settled"] = True
            result["aborted"] = result["aborted"] or event.get("aborted") is True
    return result


def _communicate(proc, prompt, timeout, cancel):
    from . import engines
    deadline = time.monotonic() + timeout
    pending = prompt
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise engines.Cancelled()
            if time.monotonic() >= deadline:
                raise engines.EngineError(f"Pi Agent exceeded {timeout} seconds")
            try:
                out, err = proc.communicate(pending, timeout=.2)
                if proc.returncode and not out:
                    raise engines.EngineError(err[-500:] or f"Pi Agent exited with code {proc.returncode}")
                return out
            except subprocess.TimeoutExpired:
                pending = None
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()


def run(c, prompt, cwd, images=(), cancel=None, meter=None):
    from . import engines, qa_capabilities
    if cancel is not None and cancel.is_set():
        raise engines.Cancelled()
    exe = path(c)
    if not exe:
        raise engines.EngineError("Pi Agent command not found; install and configure Pi first")
    try:
        selected = qa_capabilities.validate("pi", c.get("_qa_policy") or {})
        with qa_capabilities.invocation("pi", selected, prompt) as (skill_args, prompt):
            args = build_args(exe, c, images, skill_args)
            proc = engines._popen(args, Path(cwd))
            try:
                out = _communicate(proc, prompt, int(c.get("timeout") or 1200), cancel)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
            result = parse(out)
    except (ValueError, OSError) as error:
        raise engines.EngineError(str(error)) from None
    if meter is not None:
        for used in result["usage"]:
            meter.add(**used)
    if result["model"]:
        if c.get("_report_model"):
            c["_report_model"](result["model"])
        if not c.get("model"):
            from ..chat.chat_models import remember
            remember("last-default:pi", result["model"])
    if result["aborted"] or (cancel is not None and cancel.is_set()):
        raise engines.Cancelled()
    if proc.returncode:
        raise engines.EngineError(result["error"] or f"Pi Agent exited with code {proc.returncode}")
    if result["stop_reason"] == "length":
        raise engines.OutputLimitReached("Pi Agent reached the output token limit")
    if result["error"] or result["stop_reason"] == "error":
        raise engines.EngineError(result["error"] or "Pi Agent failed")
    if not result["settled"] or result["stop_reason"] != "stop" or not result["text"]:
        raise engines.EngineError("Pi Agent did not return a completed answer")
    return result["text"]

"""Pi JSON-mode policy, native session files and event adaptation (Pi 1.5.2/1.6).

Only session JSONL belongs to EasyRead's private state. Pi's agent directory,
credentials, relative resources and project trust remain native, never copied.
Workspace sessions always use full native CLI resources and project trust.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from . import session_cli as cli

CAPABILITIES = {"goal": False, "steer": False, "user_input": False, "adapter": "pi_json",
                "sandbox": False, "project_mcp": False, "native_configuration": True}
_MODEL = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_@~][A-Za-z0-9_./:@~+%-]*")


def model(value):
    if value in (None, ""):
        return ""
    # Exact provider/id; never let fuzzy aliases or a thinking suffix change it.
    if (not isinstance(value, str) or len(value) > 120 or not _MODEL.fullmatch(value)
            or value.rsplit(":", 1)[-1] in ("off", "minimal", "low", "medium", "high", "xhigh", "max")):
        raise cli.SessionError("invalid_parameter", "Pi model must be a fully qualified provider/id")
    return value


def argv(*, exe, native_id, resume, cli_home, cwd, selected_model="", reasoning_effort="",
         interactive=False):
    from . import session_reasoning
    model(selected_model)
    if cli_home is None or cwd is None:
        raise cli.SessionError("invalid_parameter", "Pi requires an explicit session directory and working directory")
    args = [exe] + ([] if interactive else ["--mode", "json"])
    args += ["--session-dir", str(Path(cli_home) / "sessions")]
    if resume:
        path = transcript(cli_home, native_id, cwd)
        if not path:
            raise cli.SessionError("not_found", "Pi session file is missing", 409)
        args += ["--session", str(path)]
    elif native_id:
        args += ["--session-id", native_id]
    if selected_model:
        provider, mid = selected_model.split("/", 1)
        args += ["--provider", provider, "--model", mid]
    args += session_reasoning.argv("pi", reasoning_effort)
    return args


def records(text):
    try:
        rows = [json.loads(line) for line in text.split("\n") if line.strip()]
        if any(not isinstance(row, dict) or not isinstance(row.get("type"), str) for row in rows):
            raise ValueError()
        return rows
    except ValueError:
        raise cli.SessionError("invalid_transcript", "Malformed Pi transcript") from None


def validate(text, native_id=None):
    rows = records(text)
    if (not rows or rows[0].get("type") != "session" or rows[0].get("version") != 3
            or not cli.valid_native_id(rows[0].get("id"))
            or (native_id is not None and rows[0]["id"] != native_id)
            or not isinstance(rows[0].get("cwd"), str) or not rows[0]["cwd"]
            or any(row["type"] == "session" for row in rows[1:])):
        raise cli.SessionError("invalid_transcript", "Invalid Pi session header (expected v3)")
    seen = set()
    for row in rows[1:]:
        rid, parent = row.get("id"), row.get("parentId")
        if (not isinstance(rid, str) or not rid or rid in seen or "parentId" not in row
                or (parent is not None and (not isinstance(parent, str) or parent not in seen))):
            raise cli.SessionError("invalid_transcript", "Invalid Pi session tree")
        seen.add(rid)
        if row["type"] == "message":
            message = row.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), (str, list)):
                raise cli.SessionError("invalid_transcript", "Invalid Pi message")
    return rows


def transcript(cli_home, native_id, cwd):
    if not cli.valid_native_id(native_id):
        return None
    matches = []
    for path in (Path(cli_home) / "sessions").glob(f"*_{native_id}.jsonl"):
        rows = validate(cli.read_transcript(path), native_id)
        if Path(rows[0]["cwd"]).resolve() == Path(cwd).resolve():
            matches.append(path)
    if len(matches) > 1:
        raise cli.SessionError("native_conflict", "Multiple Pi sessions use the same ID", 409)
    return matches[0] if matches else None


def project_transcripts(cli_home, cwd):
    found = {}
    for path in (Path(cli_home) / "sessions").glob("*.jsonl"):
        text = cli.read_transcript(path)
        complete = text[:text.rfind("\n") + 1]
        if not complete:
            continue
        header = validate(complete)[0]
        if Path(header["cwd"]).resolve() == Path(cwd).resolve():
            if header["id"] in found:
                raise cli.SessionError("native_conflict", "Multiple Pi sessions use the same ID", 409)
            found[header["id"]] = path
    return found


def text_content(content):
    if isinstance(content, str):
        return content
    return "\n".join(block["text"] for block in content if isinstance(block, dict)
                     and block.get("type") == "text" and isinstance(block.get("text"), str)) if isinstance(content, list) else ""


def messages(text):
    # Show append-only conversation history (all branches), not model context.
    complete = text[:text.rfind("\n") + 1]
    out = []
    for row in records(complete):
        message = row.get("message") if row["type"] == "message" else None
        if isinstance(message, dict) and message.get("role") in ("user", "assistant"):
            text = text_content(message.get("content"))
            if text:
                out.append({"role": message["role"], "content": text, "at": row.get("timestamp") or cli.now_iso()})
    return out


def relocate(text, native_id, cwd):
    rows = validate(text)
    rows[0].update(id=native_id, cwd=str(Path(cwd)))
    # A fork origin is not portable and must not point at a different host.
    rows[0].pop("parentSession", None)
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def _usage(usage):
    usage = usage if isinstance(usage, dict) else {}
    cached = cli._int(usage.get("cacheRead"))
    cost = usage.get("cost") or {}
    cost = cost.get("total") if isinstance(cost, dict) else None
    return {"input": cli._int(usage.get("input")) + cached + cli._int(usage.get("cacheWrite")),
            "cached": cached, "output": cli._int(usage.get("output")),
            "cost_usd": cost if isinstance(cost, (int, float)) else None}


class Events:
    """Translate only public text/tool records; never forward thinking blocks."""

    def __init__(self):
        self.blocks = {}
        self.completed = ""
        self.stop_reason = ""
        self.totals = {"input": 0, "cached": 0, "output": 0, "cost_usd": None}

    def add_usage(self, stream, usage):
        value = _usage(usage)
        for key in ("input", "cached", "output"):
            self.totals[key] += value[key]
        if value["cost_usd"] is not None:
            self.totals["cost_usd"] = (self.totals["cost_usd"] or 0) + value["cost_usd"]
        stream.usage = dict(self.totals)

    def feed(self, stream, event):
        kind = event.get("type")
        if kind == "thinking_level_changed":
            from .session_reasoning import LEVELS
            if event.get("level") in LEVELS["pi"]:
                stream.reasoning_effort = event["level"]
            return []
        if kind == "session" and cli.valid_native_id(event.get("id")):
            stream.native_id = event["id"]
            return [{"kind": "init", "session_id": stream.native_id}]
        if kind == "message_start" and (event.get("message") or {}).get("role") == "assistant":
            self.blocks = {}
            stream.error = ""  # A native retry can recover a previous failed attempt.
        if kind == "message_update":
            update = event.get("assistantMessageEvent") or {}
            if update.get("type") not in ("text_delta", "text_end"):
                return []
            index = update.get("contentIndex", 0)
            if not isinstance(index, int):
                return []
            if update["type"] == "text_delta":
                self.blocks[index] = self.blocks.get(index, "") + str(update.get("delta") or "")
            else:
                self.blocks[index] = str(update.get("content") or "")
            stream.text = self.completed + "".join(self.blocks[key] for key in sorted(self.blocks))
            return []  # The journal's message projection carries deltas, not growing event snapshots.
        if kind == "message_end":
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                return []
            text = text_content(message.get("content"))
            if text:
                self.completed += text + "\n\n"
            stream.text = self.completed.rstrip()
            self.blocks = {}
            self.add_usage(stream, message.get("usage"))
            if message.get("provider") and message.get("model"):
                stream.model = str(message["provider"]) + "/" + str(message["model"])
            from .session_reasoning import LEVELS
            if message.get("thinkingLevel") in LEVELS["pi"]:
                stream.reasoning_effort = message["thinkingLevel"]
            self.stop_reason = message.get("stopReason") or ""
            if self.stop_reason in ("error", "aborted"):
                stream.error = str(message.get("errorMessage") or "Pi response failed")
            elif self.stop_reason == "length":
                stream.error = "Pi response reached the output token limit; the answer is incomplete"
            else:
                stream.error = ""
            return [{"kind": "text", "text": text}] if text else []
        if kind == "tool_execution_start":
            return [{"kind": "tool", "id": str(event.get("toolCallId") or ""),
                     "name": str(event.get("toolName") or "tool"), "detail": cli._text(event.get("args"))}]
        if kind == "tool_execution_end":
            return [{"kind": "tool_result", "id": str(event.get("toolCallId") or ""),
                     "name": str(event.get("toolName") or "tool"), "ok": not event.get("isError"),
                     "text": text_content((event.get("result") or {}).get("content"))}]
        if kind == "agent_settled":
            stream.finished = True
            if event.get("aborted"):
                stream.error = stream.error or "Pi run aborted"
            if self.stop_reason != "stop":
                stream.error = stream.error or "Pi did not return a completed assistant response"
            return [{"kind": "result", "ok": not bool(stream.error), "text": stream.text, "usage": stream.usage}]
        if kind == "auto_retry_end" and event.get("success") is False:
            stream.error = str(event.get("finalError") or "Pi retry failed")
        if kind == "compaction_end" and isinstance(event.get("result"), dict):
            self.add_usage(stream, event["result"].get("usage"))
        if kind in ("auto_retry_start", "auto_retry_end", "compaction_start", "compaction_end"):
            text = str(event.get("errorMessage") or event.get("finalError") or kind)
            stream.notices.append(text)
            return [{"kind": "notice", "subtype": kind, "text": text}]
        return []

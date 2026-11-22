"""Pi JSON-mode policy, native session files and event adaptation (Pi 1.5.2/1.6).

Only session JSONL belongs to EasyRead's private state. Pi's agent directory,
credentials, relative resources and project trust remain native, never copied.
Workspace sessions always use full native CLI resources and project trust.
"""
from __future__ import annotations

from dataclasses import dataclass
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


def _content(content, kinds):
    if isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in kinds:
            return False
        kind = block["type"]
        fields = {"text": ("text",), "image": ("data", "mimeType"),
                  "thinking": ("thinking",), "toolCall": ("id", "name")}[kind]
        if any(not isinstance(block.get(key), str) for key in fields):
            return False
        if kind == "toolCall" and not isinstance(block.get("arguments"), dict):
            return False
    return True


def _message(message):
    # Pi 1.6 core/messages.d.ts: shell and summary messages have no content.
    # Validate public envelopes, leaving native/provider/extension metadata opaque.
    if not isinstance(message, dict):
        return False
    role = message.get("role")
    kinds = {"system": ("text",), "user": ("text", "image"),
             "assistant": ("text", "thinking", "toolCall"), "toolResult": ("text", "image"),
             "custom": ("text", "image")}
    if not isinstance(role, str):
        return False
    if role in kinds:
        return (_content(message.get("content"), kinds[role]) and (role != "custom" or (
            isinstance(message.get("customType"), str) and type(message.get("display")) is bool)))
    if role == "bashExecution":
        return (all(isinstance(message.get(key), str) for key in ("command", "output"))
                and all(type(message.get(key)) is bool for key in ("cancelled", "truncated"))
                and ("exitCode" not in message or type(message["exitCode"]) in (int, float))
                and ("excludeFromContext" not in message or type(message["excludeFromContext"]) is bool)
                and ("fullOutputPath" not in message or isinstance(message["fullOutputPath"], str)))
    if role == "branchSummary":
        return (isinstance(message.get("summary"), str) and "fromId" in message
                and (message["fromId"] is None or isinstance(message["fromId"], str)))
    if role == "compactionSummary":
        return isinstance(message.get("summary"), str) and type(message.get("tokensBefore")) in (int, float)
    return False


def validate(text, native_id=None):
    return _validate_rows(records(text), native_id)


def _validate_rows(rows, native_id=None):
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
        if row["type"] == "message" and not _message(row.get("message")):
            raise cli.SessionError("invalid_transcript", "Invalid Pi message")
    return rows


def _readable(text):
    """Only an undecodable, unterminated final JSON row may be pending a write."""
    complete, separator, tail = text.rpartition("\n")
    if tail.strip():
        try:
            json.loads(tail)
        except ValueError:
            return records(complete if separator else ""), True
    return records(text), False


@dataclass
class Snapshot:
    path: Path
    rows: list[dict]
    incomplete: bool = False


def read_snapshot(path, native_id=None, *, live=False):
    from .files import read_bytes
    raw = read_bytes(path, cli.MAX_TRANSCRIPT)
    incomplete = False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        if not live or error.reason != "unexpected end of data" or error.end != len(raw):
            raise cli.SessionError("invalid_transcript", "Invalid Pi transcript encoding") from None
        text = raw[:error.start].decode("utf-8")
        incomplete = True
    rows, pending = _readable(text) if live else (records(text), False)
    if not rows and (incomplete or pending):
        raise cli.SessionError("incomplete_transcript", "Pi session header is still incomplete", 409)
    return Snapshot(path, _validate_rows(rows, native_id), incomplete or pending)


def snapshot(cli_home, native_id, cwd, *, live=False):
    if not cli.valid_native_id(native_id):
        return None
    matches = []
    for path in (Path(cli_home) / "sessions").glob(f"*_{native_id}.jsonl"):
        value = read_snapshot(path, native_id, live=live)
        if Path(value.rows[0]["cwd"]).resolve() == Path(cwd).resolve():
            matches.append(value)
    if len(matches) > 1:
        raise cli.SessionError("native_conflict", "Multiple Pi sessions use the same ID", 409)
    return matches[0] if matches else None


def transcript(cli_home, native_id, cwd):
    value = snapshot(cli_home, native_id, cwd)
    return value.path if value else None


def project_transcripts(cli_home, cwd):
    found = {}
    for path in (Path(cli_home) / "sessions").glob("*.jsonl"):
        value = read_snapshot(path, live=True)
        header = value.rows[0]
        if not path.name.endswith("_" + header["id"] + ".jsonl"):
            raise cli.SessionError("invalid_transcript", "Invalid Pi session filename")
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
    rows, _ = _readable(text)
    return _messages(_validate_rows(rows))


def _messages(rows):
    # Show append-only public chat (all branches), never native shell/context state.
    out = []
    for row in rows:
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
    # Only the structural header relocates. Preserve every remaining raw line,
    # including signed/private metadata and historical command/output paths.
    lines = text.split("\n")
    index = next(i for i, line in enumerate(lines) if line.strip())
    lines[index] = json.dumps(rows[0], ensure_ascii=False)
    return "\n".join(lines)


def sync_terminal(sessions, pid, data, record):
    """Reconcile one tracked Pi identity without making other sessions unreadable."""
    tracking = record["terminal_tracking"]
    changed = False
    warning = None
    try:
        home, root = sessions._native_home(pid, record), Path(sessions.store.root(pid))
        native_id = record.get("native_id") or ""
        if not native_id:
            candidates = set(project_transcripts(home, root)) - set(tracking.get("baseline", []))
            if len(candidates) > 1:
                raise cli.SessionError("native_conflict", "Ambiguous native session identity", 409)
            if candidates:
                native_id = candidates.pop()
                if any(r is not record and r.get("engine") == "pi" and r.get("native_id") == native_id
                       for r in data["sessions"].summaries()):
                    raise cli.SessionError("native_conflict", "Native identity already in use", 409)
                record["native_id"] = native_id
                changed = True
        value = snapshot(home, native_id, root, live=True) if native_id else None
        if value:
            messages = _messages(value.rows)
            count = tracking.get("message_count", 0)
            if len(messages) < count:
                raise cli.SessionError("native_conflict", "Native history is shorter than saved history", 409)
            if not record.get("native_started"):
                record["native_started"] = True
                changed = True
            if len(messages) > count:
                record["messages"].extend(messages[count:])
                record["events"].append({"at": cli.now_iso(), "kind": "terminal_sync",
                                         "messages": len(messages) - count})
                tracking["message_count"] = len(messages)
                record["updated"] = cli.now_iso()
                changed = True
            if value.incomplete:
                warning = {"code": "incomplete_transcript", "message":
                    "Pi transcript has an incomplete final record. Wait for native writing to finish, then retry. "
                    "Saved history and the native file are unchanged; continuation is blocked until it is complete."}
        elif (record.get("native_started") or record.get("resumed") or tracking.get("message_count")
              or record.get("native_sync_warning")):
            raise cli.SessionError("not_found", "Native transcript is missing", 409)
    except (cli.SessionError, OSError, ValueError) as error:
        code = error.code if isinstance(error, cli.SessionError) else "invalid_transcript"
        warning = {"code": code, "message":
            "Pi transcript could not be reconciled (" + code + "). Saved history and native identity are retained. "
            "Close native writing, check this session's original file/identity, then retry; do not start a replacement conversation."}
    if warning:
        if record.get("native_sync_warning") != warning:
            record["native_sync_warning"] = warning
            changed = True
    else:
        if record.pop("native_sync_warning", None):
            changed = True
        if not sessions.terminals.scope_active(pid, record["id"]):
            record.pop("terminal_tracking", None)
            changed = True
    return changed


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

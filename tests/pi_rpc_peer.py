"""Disposable Pi 1.6 JSONL peer. No provider, credentials, network or shell tools."""
import json
import os
from pathlib import Path
import sys

if "--version" in sys.argv:
    print("pi 1.6.0")
    sys.exit(0)
args = sys.argv[1:]

def arg(key):
    return args[args.index(key) + 1]

assert arg("--mode") == "rpc"
assert not any(flag in args for flag in ("--no-extensions", "--no-mcp", "--tools", "--no-skills", "--approve"))
if "--session" in args:
    path = Path(arg("--session"))
    rows = [json.loads(line) for line in path.read_text().split("\n") if line]
    assert rows[0]["cwd"] == os.getcwd()
else:
    native_id = arg("--session-id")
    path = Path(arg("--session-dir")) / ("2026-01-01T00-00-00-000Z_" + native_id + ".jsonl")
    rows = [{"type": "session", "version": 3, "id": native_id, "cwd": os.getcwd()}]
native_id = rows[0]["id"]
active = False
compacting = False
queues = {"steering": [], "followUp": []}
dialogs = {}
late = {}
turn = sum(row.get("message", {}).get("role") == "user" for row in rows)

def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)

def reply(command, data=None, error=None):
    emit({"type": "response", "id": command["id"], "command": command["type"], "success": error is None,
          **({"error": error} if error else {"data": data or {}})})

def save(role, content):
    message = {"role": role, "content": content}
    parent = rows[-1]["id"] if len(rows) > 1 else None
    rows.append({"type": "message", "id": str(len(rows)), "parentId": parent, "message": message})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    return message

def queue_event():
    emit({"type": "queue_update", **{key: [entry["message"] for entry in values] for key, values in queues.items()}})

def user(command):
    global turn
    turn += 1
    content = [{"type": "text", "text": command["message"]}, *command.get("images", [])]
    message = save("user", content)
    emit({"type": "message_start", "message": message})
    emit({"type": "message_end", "message": message})

def answer(stream=False):
    emit({"type": "message_start", "message": {"role": "assistant"}})
    emit({"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "secret-thinking"}})
    text = "a" * 300 if stream else "answer " + str(turn)
    if stream:
        import time
        for _ in range(300):
            emit({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "a"}})
            time.sleep(.004)
    else:
        emit({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": text}})
    message = save("assistant", [{"type": "text", "text": text}])
    message.update(provider="provider", model="model", stopReason="stop", usage={"input": 3, "output": 2})
    emit({"type": "message_end", "message": message})

def deliver(key, respond=True):
    command = queues[key].pop(0)
    queue_event()
    user(command)
    if respond:
        answer()

def finish(aborted=False):
    global active
    for key, values in queues.items():
        while values:
            deliver(key)
    active = False
    emit({"type": "agent_end", "willRetry": False})
    emit({"type": "agent_settled", "aborted": aborted})

for line in sys.stdin:
    command = json.loads(line)
    with open("pi-rpc-commands.jsonl", "a") as output:
        output.write(json.dumps(command) + "\n")
    kind = command["type"]
    if kind == "get_state":
        reply(command, {"sessionId": native_id, "sessionFile": str(path), "isStreaming": active,
                        "isCompacting": compacting, "pendingMessageCount": sum(len(v) for v in queues.values())})
    elif kind == "get_commands":
        reply(command, {"commands": [
            {"name": "skill:review", "description": "Review skill", "source": "skill", "sourceInfo": {"path": "/private/SKILL.md"}},
            {"name": "template", "description": "Template", "source": "prompt"},
            *({"name": name, "description": name, "source": "extension"} for name in ("dialog", "goal", "identity-change", "notice", "late", "finish-fixture"))]})
    elif kind in ("prompt", "steer", "follow_up"):
        if kind != "prompt":
            command["streamingBehavior"] = "steer" if kind == "steer" else "followUp"
        question = command["message"]
        # SSH integration replaces this line with a disposable remote receipt.
        if question.startswith("reject"):
            reply(command, error="rejected before acceptance")
        elif question.startswith("disconnect"):
            sys.exit(7)
        elif question == "malformed":
            print("{bad frame", flush=True)
        elif question.startswith("/dialog"):
            dialogs["dialog-1"] = command
            method = question.split()[1] if len(question.split()) > 1 else "confirm"
            emit({"type": "extension_ui_request", "id": "dialog-1", "method": method, "title": "Native question",
                  "message": "Allow?", "options": ["one", "two"], "prefill": "initial"})
        elif question == "/finish-fixture":
            finish()
            reply(command, {"disposition": "handled"})
        elif question == "/identity-change":
            native_id = "11111111-1111-4111-8111-111111111111"
            emit({"type": "message_start", "message": {"role": "user", "content": "WRONG SESSION"}})
            reply(command, {"disposition": "handled"})
        elif question == "/late":
            late[command["id"]] = command
        elif question == "/notice":
            emit({"type": "extension_ui_request", "id": "notice-1", "method": "set_editor_text", "text": "suggested draft"})
            reply(command, {"disposition": "handled"})
        elif question.startswith("/goal"):
            reply(command, {"disposition": "handled"})
        else:
            if question.startswith("/skill:review"):
                command["message"] = '<skill name="review" location="/private/SKILL.md">SECRET SKILL BODY</skill>\n\n' + question[13:]
            if question.startswith("/template"):
                command["message"] = "transformed template"
            if active or kind != "prompt":
                behavior = command.get("streamingBehavior")
                if behavior not in ("steer", "followUp"):
                    reply(command, error="Agent is already processing")
                    continue
                queues["steering" if behavior == "steer" else "followUp"].append(command)
                queue_event()
                reply(command, {"disposition": "queued"})
            else:
                active = True
                emit({"type": "agent_start"})
                reply(command, {"disposition": "started"})
                user(command)
                answer(stream=question == "stream-300")
                if not question.startswith("hold") and "wait-for-cancel" not in question:
                    finish()
    elif kind == "extension_ui_response":
        pending = dialogs.pop(command["id"], None)
        if pending:
            reply(pending, {"disposition": "handled"})
    elif kind == "clear_queue":
        queues = {"steering": [], "followUp": []}
        queue_event()
        reply(command)
    elif kind == "abort":
        finish(aborted=True)
        reply(command)
    elif kind == "compact":
        emit({"type": "compaction_start", "reason": "manual"})
        emit({"type": "compaction_end", "reason": "manual", "aborted": False})
        reply(command)
    elif kind == "fixture_event":
        event = command["event"]
        if event["type"] == "agent_end":
            active = False
        elif event["type"] == "agent_start":
            active = True
        emit(event)
        reply(command)
    elif kind == "fixture_deliver":
        deliver(command.get("queue", "followUp"), command.get("answer", True))
        reply(command)
    elif kind == "fixture_finish":
        finish()
        reply(command)
    elif kind == "fixture_late":
        for pending in late.values():
            reply(pending, {"disposition": "handled"})
        late.clear()
        reply(command)
    else:
        reply(command, error="unknown command")

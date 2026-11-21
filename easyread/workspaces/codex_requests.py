"""Native server requests: explicit user decisions, exact identity, no auto-grants."""
from __future__ import annotations

import json
import math

from .session_cli import SessionError
from .codex_protocol import REQUEST_METHODS, request_key


def normalize(message, generation, thread_id):
    method, params = message.get("method"), message.get("params") or {}
    rid = message.get("id")
    request_key(rid)
    if method not in REQUEST_METHODS:
        raise SessionError("unsupported_request", "Native server request is not supported", 409)
    if params.get("threadId") != thread_id:
        raise SessionError("request_scope", "Native request does not belong to this session", 409)
    kind = "question" if method == "item/tool/requestUserInput" else (
        "elicitation" if method == "mcpServer/elicitation/request" else "approval")
    card = {"id": rid, "generation": generation, "kind": kind, "thread_id": thread_id,
            "turn_id": params.get("turnId"), "item_id": params.get("itemId"),
            "is_blocking": params.get("isBlocking", True), "title": method}
    if kind == "question":
        card["questions"] = [{k: q[k] for k in ("id", "header", "question", "options", "isOther", "isSecret") if k in q}
                             for q in params.get("questions", [])]
    elif kind == "approval":
        card.update(reason=params.get("reason") or "", command=params.get("command"))
        if method == "item/permissions/requestApproval":
            # Show the exact requested grants, but never accept client-supplied grants.
            card["reason"] += "\nRequested permissions: " + json.dumps(params.get("permissions", {}), ensure_ascii=False)
        if method == "item/fileChange/requestApproval":
            card["reason"] += "\n" + str(params.get("grantRoot") or "Review the file changes in this turn")
    else:
        card.update(title=params.get("title") or params.get("serverName") or method,
                    reason=params.get("message") or params.get("description") or "", mode=params.get("mode"),
                    schema=params.get("requestedSchema"), url=params.get("url"))
    return card


def response(method, params, body):
    if method == "item/tool/requestUserInput":
        answers = body.get("answers")
        ids = {q["id"] for q in params.get("questions", [])}
        if not isinstance(answers, dict) or set(answers) != ids:
            raise SessionError("invalid_answer", "Answer each requested question explicitly")
        for answer in answers.values():
            if (not isinstance(answer, dict) or set(answer) != {"answers"} or not isinstance(answer["answers"], list)
                    or not answer["answers"] or any(not isinstance(s, str) or len(s) > 200000 for s in answer["answers"])):
                raise SessionError("invalid_answer", "Question answers must be non-empty arrays of strings")
        return {"answers": answers}
    decision = body.get("decision")
    if decision not in ("accept", "decline"):
        raise SessionError("invalid_answer", "Choose accept or decline; persistent grants are not supported")
    if method == "item/permissions/requestApproval":
        return {"permissions": params.get("permissions", {}) if decision == "accept" else {}, "scope": "turn"}
    if method != "mcpServer/elicitation/request":
        available = params.get("availableDecisions")
        if available and decision not in available:
            raise SessionError("invalid_answer", "This decision is not offered by the native runtime")
        return {"decision": decision}
    if decision == "decline":
        return {"action": "decline"}
    if params.get("mode") == "url":
        return {"action": "accept"}
    if params.get("mode") not in ("form", "openai/form", "openaiForm"):
        raise SessionError("unsupported_form", "This native verification flow is unsupported; you may decline", 409)
    values, schema = body.get("values"), params.get("requestedSchema")
    validate_form(schema, values)
    return {"action": "accept", "content": values}


def validate_form(schema, values):
    """Deliberately small MCP form subset; unsupported constraints fail closed."""
    def unsupported():
        raise SessionError("unsupported_form", "Unsupported elicitation schema; decline or use a native client", 409)

    def invalid():
        raise SessionError("invalid_answer", "Elicitation values do not match the requested schema")

    if (not isinstance(schema, dict) or schema.get("type") != "object"
            or not isinstance(schema.get("properties"), dict)
            or set(schema) - {"$schema", "type", "properties", "required", "title", "description", "additionalProperties"}):
        unsupported()
    properties = schema["properties"]
    if not isinstance(values, dict) or set(values) - set(properties) or set(schema.get("required") or []) - set(values):
        invalid()
    types = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,), "array": (list,)}
    for key, value in values.items():
        rule = properties[key]
        if (not isinstance(rule, dict) or rule.get("type") not in types or set(rule) - {
                "type", "title", "description", "default", "enum", "enumNames", "oneOf", "items",
                "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems"}):
            unsupported()
        if type(value) not in types[rule["type"]]:
            invalid()
        if type(value) in (float, int) and not math.isfinite(value):
            invalid()
        choices = rule.get("enum")
        if "oneOf" in rule:
            options = rule["oneOf"]
            if not isinstance(options, list) or any(not isinstance(o, dict) or "const" not in o or set(o) - {"const", "title"} for o in options):
                unsupported()
            choices = [o["const"] for o in options]
        if choices is not None and value not in choices:
            invalid()
        for low, high, actual in (("minLength", "maxLength", len(value) if isinstance(value, str) else None),
                                  ("minItems", "maxItems", len(value) if isinstance(value, list) else None),
                                  ("minimum", "maximum", value if type(value) in (int, float) else None)):
            if actual is not None and ((rule.get(low) is not None and actual < rule[low]) or
                                       (rule.get(high) is not None and actual > rule[high])):
                invalid()
        if isinstance(value, list):
            items = rule.get("items") or {}
            if "enum" in items and set(items) <= {"type", "enum"}:
                choices = items["enum"]
            elif "anyOf" in items and set(items) == {"anyOf"} and all(isinstance(o, dict) and "const" in o and not set(o) - {"const", "title"} for o in items["anyOf"]):
                choices = [o["const"] for o in items["anyOf"]]
            else:
                unsupported()
            if any(type(v) is not str or v not in choices for v in value):
                invalid()

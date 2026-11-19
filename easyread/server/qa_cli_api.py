"""Authenticated settings endpoints for Q&A-only CLI capabilities and skills."""
import json
import re
from urllib.parse import unquote, urlsplit

from ..app import config, native_cli_settings, qa_skills
from ..engines import qa_capabilities
from ..library.store import write_json_atomic
from .access_routes import same_origin

_PATTERN = re.compile(r"^/api/chat/cli/(claude|codex)(?:/models/([^/]+))?(/skills/import)?$")


def listing(engine, model_id=None):
    cfg = config.load()
    entry = qa_capabilities.model_entry(cfg, engine, model_id) if model_id else None
    selected = qa_capabilities.policy(cfg, engine, model_id)
    items = qa_skills.inventory(engine)
    for item in items:
        item["enabled"] = qa_capabilities.skill_enabled(engine, item, selected)
        item["inherited_enabled"] = qa_capabilities.skill_enabled(engine, item, {**selected, "skill_overrides": {}})
    return {"engine": engine, "model_id": model_id or "", "model_name": (entry.get("name") or entry.get("model") or model_id) if entry else "",
            "inherit": bool(model_id and qa_capabilities.model_override(cfg, engine, model_id) is None),
            "defaults": qa_capabilities.policy(cfg, engine), "settings": selected, "skills": items,
            "native": native_cli_settings.summary(engine),
            "tools": qa_capabilities.CLAUDE_TOOLS + ["Skill", "Bash", "Write", "Edit", "Agent"] if engine == "claude" else qa_capabilities.CODEX_TOOLS}


def _handle(handler):
    url = urlsplit(handler.path)
    match = _PATTERN.fullmatch(url.path)
    if not match:
        return False
    engine, model_id, action = match.groups()
    model_id = unquote(model_id) if model_id else None
    if model_id is not None and len(model_id) > 128:
        handler.close_connection = True
        handler._json(400, {"error": "Invalid model_id"})
        return True
    if handler.command in ("GET", "HEAD") and not action:
        handler._json(200, listing(engine, model_id))
        return True
    if handler.command != "POST":
        handler._json(405, {"error": "Method not allowed"})
        return True
    if not same_origin(handler):
        handler.close_connection = True
        handler._json(403, {"error": "bad origin"})
        return True
    if int(handler.headers.get("Content-Length", "0")) > (8 * 1024 * 1024 if action else 65536):
        raise ValueError("CLI settings upload is too large")
    body = json.loads(handler._body() or b"{}")
    if not isinstance(body, dict):
        raise ValueError("Expected object")
    if action:
        if model_id:
            qa_capabilities.model_entry(config.load(), engine, model_id)
        qa_skills.import_skill(engine, body.get("filename", ""), body.get("content_b64", ""))
    else:
        inherit = bool(model_id) and set(body) == {"inherit"} and body["inherit"] is True
        selected = None if inherit else qa_capabilities.validate(engine, body)
        with config.SAVE_LOCK:
            # Replace only the chosen policy; never deep-merge stale skill overrides.
            saved = config._merge(config.DEFAULTS, config._saved())
            if model_id:
                qa_capabilities.model_entry(saved, engine, model_id)
                policies = saved.setdefault("qa_model_policies", {})
                if inherit:
                    policies.pop(model_id, None)
                else:
                    policies[model_id] = {"engine": engine, "settings": selected}
            else:
                saved.setdefault("qa_cli", {})[engine] = selected
            write_json_atomic(config.CONFIG_PATH, saved)
    handler._json(200, listing(engine, model_id))
    return True


def handle(handler):
    try:
        return _handle(handler)
    except qa_capabilities.ModelPolicyNotFound as error:
        handler.close_connection = True
        handler._json(404, {"error": str(error)})
        return True

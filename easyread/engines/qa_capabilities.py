"""Opt-in capabilities for user Q&A only; never consulted by translation."""
from __future__ import annotations

import copy
import json
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ..app.i18n import tr

ENGINES = ("claude", "codex")
CLAUDE_TOOLS = ["Read", "Glob", "Grep", "WebSearch", "WebFetch"]
CODEX_TOOLS = ["shell", "web_search", "view_image", "update_plan"]
DEFAULT = {"mode": "default", "skills": False, "mcp": False, "web_search": True, "web_fetch": True,
           "allowed_tools": [], "sandbox": "read-only", "skill_overrides": {}}
_TOOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_.*:-]*(?:\([^\r\n\x00]{1,160}\))?$")


def validate(engine, value):
    if engine not in ENGINES or not isinstance(value, dict):
        raise ValueError("Unsupported CLI capability settings")
    unknown = set(value) - set(DEFAULT)
    if unknown:
        raise ValueError("Unknown CLI capability fields")
    out = copy.deepcopy(DEFAULT)
    out.update(value)
    if out["mode"] not in ("default", "full", "custom"):
        raise ValueError("Invalid Q&A capability mode")
    for key in ("skills", "mcp", "web_search", "web_fetch"):
        if type(out[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    if out["sandbox"] not in ("read-only", "workspace-write", "inherit"):
        raise ValueError("Invalid Codex sandbox")
    tools = out["allowed_tools"]
    if not isinstance(tools, list) or len(tools) > 60 or any(not isinstance(t, str) or not _TOOL.fullmatch(t) for t in tools):
        raise ValueError(tr("允许工具须为有效工具名或权限规则，每行一个"))
    if engine == "codex" and any(t not in CODEX_TOOLS for t in tools):
        raise ValueError(tr("Codex 仅支持列表中的原生能力开关，不支持 Claude 工具规则"))
    out["allowed_tools"] = list(dict.fromkeys(tools))
    overrides = out["skill_overrides"]
    if not isinstance(overrides, dict) or len(overrides) > 300 or any(not isinstance(k, str) or len(k) > 100 or type(v) is not bool for k, v in overrides.items()):
        raise ValueError("Invalid skill enablement rules")
    return out


class ModelPolicyNotFound(ValueError):
    pass


def model_entry(cfg, engine, model_id):
    from ..chat import chat_models
    entry = next((m for m in chat_models.models(cfg) if m.get("id") == model_id and m.get("engine") == engine), None)
    if entry is None:
        raise ModelPolicyNotFound(tr("找不到对应的模型条目，请先保存模型"))
    return entry


def model_override(cfg, engine, model_id):
    if not model_id:
        return None
    model_entry(cfg, engine, model_id)
    record = (cfg.get("qa_model_policies") or {}).get(model_id)
    if record is not None and not isinstance(record, dict):
        raise ValueError("Invalid model Q&A policy")
    if record and record.get("engine") == engine:
        if not isinstance(record.get("settings"), dict):
            raise ValueError("Invalid model Q&A policy")
        return record["settings"]
    return None


def policy(cfg, engine, model_id=None):
    raw = model_override(cfg, engine, model_id) if model_id else None
    if raw is None:
        raw = (cfg.get("qa_cli") or {}).get(engine) or {}
    return validate(engine, raw)


def prepare(cfg):
    """Called only by explicitly opted-in Q&A entry points."""
    out = copy.deepcopy(cfg)
    engine = cfg.get("engine")
    if engine not in ENGINES:
        return out
    selected = policy(cfg, engine, cfg.get("_qa_model_id"))
    previous_policy = "_qa_policy" in out[engine]
    for key in list(out[engine]):
        if key.startswith("_qa_"):
            out[engine].pop(key)
    if previous_policy:
        out[engine].pop("_chat", None)
        out[engine]["lean"] = True
    if selected["mode"] != "default":
        out[engine]["_qa_policy"] = selected
        out[engine]["lean"] = False
        out[engine]["_chat"] = True
    return out


def _native(engine):
    from ..app import native_cli_settings
    return native_cli_settings.read(engine)


def skill_enabled(engine, item, selected):
    if item["id"] in selected["skill_overrides"]:
        return selected["skill_overrides"][item["id"]]
    if item["managed"]:
        return True
    native = _native(engine)
    if engine == "claude":
        return (native.get("skillOverrides") or {}).get(item["name"]) != "off"
    enabled = True
    for rule in (native.get("skills") or {}).get("config", []):
        if not isinstance(rule, dict):
            continue
        if rule.get("name") == item["name"] or str(rule.get("path", "")) == item["path"]:
            enabled = rule.get("enabled") is not False
    return enabled


def _skills(engine, selected):
    from ..app import qa_skills
    return qa_skills.inventory(engine)


def _native_skill_args(engine, selected, items):
    enabled = selected["mode"] == "full" or selected["skills"]
    native = [item for item in items if not item["managed"]]
    if engine == "claude":
        if not enabled:
            return ["--disable-slash-commands"]
        overrides = {item["name"]: ("on" if skill_enabled(engine, item, selected) else "off")
                     for item in native if item["id"] in selected["skill_overrides"]}
        return ["--settings", json.dumps({"skillOverrides": overrides})] if overrides else []
    rules = []
    for item in native:
        if not enabled or item["id"] in selected["skill_overrides"]:
            rules.append('{path=' + json.dumps(item["path"]) + ',enabled=' + str(enabled and skill_enabled(engine, item, selected)).lower() + '}')
    args = ["-c", "skills.config=[" + ",".join(rules) + "]"] if rules else []
    if not enabled:
        args += ["-c", "skills.include_instructions=false", "-c", "skills.bundled.enabled=false", "-c", "features.skill_search=false"]
    elif selected["mode"] == "custom":
        args += ["-c", "skills.include_instructions=true", "-c", "features.skill_search=true"]
    return args


def claude_args(selected):
    if selected["mode"] == "full":
        return []  # Native CLI config, permissions, MCP and tools remain in force.
    rules = selected["allowed_tools"]
    builtins = list(dict.fromkeys(rule.split("(", 1)[0] for rule in rules if not rule.startswith("mcp__")))
    builtins = [name for name in builtins if name not in ("WebSearch", "WebFetch", "Skill")]
    if selected["web_search"]:
        builtins.append("WebSearch")
    if selected["web_fetch"]:
        builtins.append("WebFetch")
    if selected["skills"]:
        builtins.append("Skill")
    allowed = [rule for rule in rules if rule.split("(", 1)[0] not in ("WebSearch", "WebFetch", "Skill")]
    allowed += [name for name in ("WebSearch", "WebFetch", "Skill") if name in builtins]
    args = ["--tools", ",".join(builtins)]
    if allowed:
        args += ["--allowedTools", ",".join(allowed)]
    denied = [name for name, enabled in (("WebSearch", selected["web_search"]), ("WebFetch", selected["web_fetch"]), ("Skill", selected["skills"])) if not enabled]
    if not selected["mcp"]:
        args += ["--strict-mcp-config"]
        denied.append("mcp__*")
    if denied:
        args += ["--disallowedTools", ",".join(denied)]
    return args


def codex_args(selected, cwd=None, executable="codex"):
    if selected["mode"] == "full":
        return []
    tools = selected["allowed_tools"]
    args = []
    if selected["sandbox"] != "inherit":
        args += ["--sandbox", selected["sandbox"]]
    args += ["-c", "features.shell_tool=" + str("shell" in tools).lower(),
             "-c", "features.view_image=" + str("view_image" in tools).lower(),
             "-c", 'web_search="' + ("live" if selected["web_search"] and "web_search" in tools else "disabled") + '"']
    # Plugin/app/code-generation capabilities are native only in full mode.
    for feature in ("apps", "plugins", "multi_agent", "image_generation", "computer_use", "browser_use", "browser_use_external"):
        args += ["-c", f"features.{feature}=false"]
    args += ["-c", "tools.update_plan.enabled=" + str("update_plan" in tools).lower()]
    if selected["web_search"] and "web_search" in tools:
        args += ["-c", "features.code_mode_host=true"]
    if not selected["mcp"]:
        from ..app import native_cli_settings
        native = native_cli_settings.codex_effective(cwd, executable) if cwd is not None else _native("codex")
        for name in (native.get("mcp_servers") or {}):
            args += ["-c", f"mcp_servers.{json.dumps(name)}.enabled=false"]
    return args


@contextmanager
def invocation(engine, selected, prompt):
    """Apply skill selection for this call, with no changes to native skill roots."""
    items = _skills(engine, selected)
    active = selected["mode"] == "full" or selected["skills"]
    managed = [item for item in items if item["managed"] and active and skill_enabled(engine, item, selected)]
    args = _native_skill_args(engine, selected, items)
    with tempfile.TemporaryDirectory(prefix="easyread-qa-skills-") as directory:
        if engine == "claude" and managed:
            root = Path(directory)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin/plugin.json").write_text('{"name":"easyread-qa"}', encoding="utf-8")
            (root / "skills").mkdir()
            for item in managed:
                import shutil
                shutil.copytree(Path(item["path"]).parent, root / "skills" / item["name"], symlinks=False)
            args += ["--plugin-dir", str(root)]
        elif engine == "codex" and managed:
            # codex exec has no extra skill-root flag. Supply an explicit QA-only
            # catalog, following the same SKILL.md progressive-disclosure format.
            catalog = [{"name": item["name"], "description": item["description"][:1200], "path": item["path"]} for item in managed]
            if selected["mode"] == "custom" and "shell" not in selected["allowed_tools"]:
                # Instruction-only skills remain useful without granting shell access.
                for entry in catalog:
                    entry["instructions"] = Path(entry["path"]).read_text(encoding="utf-8-sig")
            if len(json.dumps(catalog, ensure_ascii=False)) > 64000:
                raise ValueError(tr("问答 Skill 目录过大，请关闭部分技能后重试"))
            prompt += "\n\n## EasyRead Q&A skills\nThese user-installed skills are available for this answer only. " \
                      "When the question requests $name or matches a description, use inline instructions if provided, otherwise read the corresponding SKILL.md, and follow its workflow " \
                      "using permitted tools. Resolve relative references/scripts against its directory. " \
                      "If a required tool is unavailable, explain the limitation rather than pretending to run it.\n" + json.dumps(catalog, ensure_ascii=False)
        yield args, prompt


def prompt_guidance(selected):
    if selected["mode"] == "full":
        return "\n\nUse the available CLI tools/skills and native permissions when helpful for this question. Cite external sources; do not claim actions you did not perform."
    return "\n\nUse only available tools and permitted skills for this answer. Respect the configured sandbox and native CLI permission rules; explain any missing capability. Cite external sources."

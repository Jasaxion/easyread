"""设置页的保存类接口（POST）。server.py 按路径转过来，每个函数收请求体、返回要回给页面的 JSON。"""
from __future__ import annotations

import copy
from urllib.parse import urlsplit

from ..chat import chat_models
from ..app import config, proxy
from ..app.api_keys import resolve, saved_keys
from ..engines import engines, openai_api
from ..translate import langs, instructions, prompt_style
from ..app.i18n import tr


def save_config(patch: dict) -> dict:
    patch.pop("docker_terminal", None)
    patch.pop("embedded_terminal", None)  # Runtime capabilities cannot be changed through config.
    if "access" in patch or "access_code" in patch:
        raise ValueError("Use /api/access to change access protection")
    if "qa_cli" in patch or "qa_model_policies" in patch or "_qa_model_id" in patch:
        raise ValueError("Use /api/chat/cli/<engine> to change Q&A capabilities")
    if "system_proxy" in patch:
        raise ValueError("Use /api/system-proxy to change global proxies")
    patch.pop("translation_prompt_default", None)
    patch.pop("translation_prompt_effective", None)
    patch.pop("translation_extra", None)  # Task-only instructions must never become global.
    if "translation_prompt" in patch:
        patch["translation_prompt"] = prompt_style.validate(patch["translation_prompt"])
    if "translate_figure_text" in patch and type(patch["translate_figure_text"]) is not bool:
        raise ValueError("translate_figure_text must be a boolean")
    if "auto_translate_after_prepare" in patch and type(patch["auto_translate_after_prepare"]) is not bool:
        raise ValueError("auto_translate_after_prepare must be a boolean")
    if "translation_instructions" in patch:
        patch["translation_instructions"] = instructions.validate(patch["translation_instructions"])
    patch.pop("library_dir", None)
    if "page_cap" in patch and (type(patch["page_cap"]) is not int or patch["page_cap"] < 0):
        raise ValueError(tr("页数确认上限必须是非负整数"))
    if "paper_concurrency" in patch and (type(patch["paper_concurrency"]) is not int
                                         or not 1 <= patch["paper_concurrency"] <= 4):
        raise ValueError(tr("同时翻译的论文数必须是 1 到 4 的整数"))
    if "target" in patch:
        patch["target"] = langs.valid(patch["target"])
    if isinstance(patch.get("openai"), dict):
        patch["openai"] = config.with_key(patch["openai"])
    if "figure_images" in patch:
        options = patch["figure_images"]
        if not isinstance(options, dict):
            raise ValueError("figure_images must be an object")
        if "enabled" in options and type(options["enabled"]) is not bool:
            raise ValueError("figure_images.enabled must be a boolean")
        if "provider" in options and options["provider"] not in ("codex", "api"):
            raise ValueError("figure_images.provider must be codex or api")
        for key in ("base_url", "api_key", "model"):
            if key in options:
                if not isinstance(options[key], str):
                    raise ValueError(f"figure_images.{key} must be a string")
                options[key] = options[key].strip()
        if options.get("base_url"):
            url = urlsplit(options["base_url"])
            if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password:
                raise ValueError(tr("译图接口地址必须是 HTTP 或 HTTPS 地址"))
        if "timeout" in options and (type(options["timeout"]) is not int or options["timeout"] <= 0):
            raise ValueError("figure_images.timeout must be a positive integer")
        patch["figure_images"] = config.with_key(options, "figure_images")
    return {"config": config.public(config.save(patch))}


def save_chat_models(body: dict) -> dict:
    """保存“问 AI”的名单和默认模型；或面板里只改默认。"""
    if "append" in body:
        item = body["append"]
        if "models" in body or not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError("Invalid model append request")
        if item.get("engine") not in (*engines.CLI_ENGINES, "openai"):
            raise ValueError("Unknown model engine")
        sanitized = chat_models.sanitize([item])
        if len(sanitized) != 1 or sanitized[0]["id"] != item["id"] or sanitized[0]["engine"] != item["engine"]:
            raise ValueError("Invalid model entry")
        with config.SAVE_LOCK:
            cfg = config.load()
            existing = chat_models.models(cfg)
            found = next((m for m in existing if m.get("id") == item["id"]), None)
            if found and any((found.get(k) or "") != (sanitized[0].get(k) or "") for k in ("engine", "model", "preset", "base_url", "api", "reasoning_effort", "service_tier")):
                raise ValueError(tr("模型条目已变化，请重新打开设置"))
            if found is None:
                config.save({"chat": {"models": [*existing, sanitized[0]]}})
        return chat_models.listing(config.load())
    patch = {}
    if "models" in body:
        patch["models"] = chat_models.sanitize(body["models"])
    if body.get("default"):
        patch["default"] = str(body["default"])
    full = {"chat": patch}
    if isinstance(body.get("keys"), dict):  # 设置里给某家 API 填的 Key，和翻译那边共用
        keys = saved_keys(config.load()["openai"])
        keys.update({str(k): str(v).strip() for k, v in body["keys"].items() if v and not str(v).startswith("••••")})
        full["openai"] = {"keys": keys}
    config.save(full)
    return chat_models.listing(config.load())


def test_engine(body: dict) -> dict:
    cfg = copy.deepcopy(config.load())
    engine = body.get("engine") or cfg["engine"]
    if engine not in (*engines.CLI_ENGINES, "openai", "none"):
        raise ValueError("Unknown model engine")
    cfg["engine"] = engine
    if engine == "openai" and "openai" in body:
        supplied = body["openai"]
        if not isinstance(supplied, dict):
            raise ValueError("Expected API model settings")
        allowed = {"preset", "base_url", "api", "model", "api_key", "vision", "timeout"}
        options = {key: value for key, value in supplied.items() if key in allowed}
        cfg[engine] = {**cfg[engine], **config.with_key(options)}
    if engine != "none":
        for key in ("model", "reasoning_effort", "service_tier"):
            if key in body:
                if not isinstance(body[key], str):
                    raise ValueError("Model options must be strings")
                cfg[engine][key] = body[key]
    return engines.test(cfg)


def list_models(body: dict) -> dict:
    """设置页“获取模型列表”：{base_url, preset, api_key}；Key 留空或打码时用这家已存的。"""
    key = str(body.get("api_key") or "").strip()
    if not key or key.startswith("••••"):
        key = resolve(config.load()["openai"], str(body.get("preset") or ""), str(body.get("base_url") or ""))
    try:
        return {"ok": True, "models": openai_api.models({"base_url": body.get("base_url"), "api_key": key})}
    except engines.EngineError as e:
        return {"ok": False, "message": str(e)[:300]}


def save_system_proxy(body: dict) -> dict:
    with config.SAVE_LOCK:
        settings = proxy.validate(body, config.load().get("system_proxy"))
        cfg = config.save({"system_proxy": settings})
        proxy.apply(settings)
        return {"config": config.public(cfg)}


def test_system_proxy(body: dict) -> dict:
    return proxy.test(proxy.validate(body, config.load().get("system_proxy")))


POST = {"/api/system-proxy": save_system_proxy, "/api/system-proxy/test": test_system_proxy,
        "/api/config": save_config, "/api/chat/models": save_chat_models,
        "/api/config/test": test_engine, "/api/models/list": list_models}

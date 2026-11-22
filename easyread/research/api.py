from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

from ..chat import chat_models
from ..app import config
from ..app import prefs
from ..app.i18n import tr
from . import comparison, comparison_store, proposals, tasks
from .store import normalize_folders, set_vault, settings, vault_path

PREFIX = "/api/research/v1"


def get(app, request_path: str):
    parsed = urlparse(request_path)
    path = unquote(parsed.path)
    if not path.startswith(PREFIX):
        return None
    endpoint = path[len(PREFIX):] or "/status"
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    if endpoint == "/monitor":
        from ..server.task_notices import monitor
        return monitor(app)
    if endpoint == "/status":
        vault = vault_path()
        cfg = config.load()
        scope = tasks.scope_id(app.lib.root)
        models = [{"id": item.get("id"), "name": item.get("name"), "engine": item.get("engine")}
                  for item in chat_models.models(cfg)]
        return {"vault": str(vault) if vault else "", "configured": bool(vault), "model": settings().get("model") or (cfg.get("chat") or {}).get("default", ""),
                "models": models, "papers": len(app.lib.all()), "scope_id": scope,
                "tasks": tasks.list_tasks(scope=scope),
                "pending_comparison": comparison_store.latest(app.lib.root, pending=True) or proposals.latest_pending_comparison(),
                "latest_comparison": comparison_store.latest(app.lib.root) or proposals.latest_comparison()}
    if endpoint == "/comparisons":
        return {"comparisons": sorted(comparison_store.list_comparisons(app.lib.root) + proposals.list_comparisons(),
                                      key=lambda item: item.get("created", 0), reverse=True)}
    if endpoint.startswith("/tasks/"):
        task = tasks.get(endpoint.rsplit("/", 1)[1], scope=tasks.scope_id(app.lib.root))
        if task is None:
            raise ValueError(tr("找不到这个研究工作。"))
        return task
    if endpoint.startswith("/proposals/") and not endpoint.endswith("/review") and not endpoint.endswith("/title"):
        identity = endpoint.rsplit("/", 1)[1]
        return comparison_store.get(app.lib.root, identity) or proposals.get(identity)
    raise ValueError(tr("找不到这个 Research API 路径。"))


def post(app, endpoint: str, body: dict):
    if endpoint.startswith("/activities/") and endpoint.endswith("/ack") and len(endpoint.split("/")) == 4:
        from ..server.task_notices import acknowledge
        return acknowledge(app, endpoint.split("/")[2], body)
    if endpoint == "/query":
        from . import index
        query = body.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 3000:
            raise ValueError(tr("请输入不超过 3000 字的研究检索词。"))
        kind = body.get("kind") or ""
        if kind not in ("", "paper", "topic", "concept", "question", "hypothesis", "comparison", "reader-note"):
            raise ValueError(tr("不支持的研究检索类型。"))
        limit = body.get("limit", 10)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 30:
            raise ValueError(tr("研究检索条数必须在 1 到 30 之间。"))
        return index.search(query, limit=limit, kind=kind, library=app.lib)
    if endpoint == "/setup":
        path = set_vault(str(body.get("vault") or "").strip(), body.get("folders"))
        return {"vault": str(path), "configured": True, "notes": proposals.list_notes()}
    if endpoint == "/settings":
        patch = {}
        if "model" in body:
            model_id = str(body["model"] or "")
            if model_id:
                chat_models.find(config.load(), model_id)
            patch["model"] = model_id
        if "folders" in body:
            patch["folders"] = normalize_folders(body["folders"])
        if patch:
            prefs.save({"research": patch})
        return {"model": settings().get("model", "")}
    if endpoint.startswith("/tasks/") and endpoint.endswith("/cancel"):
        task = tasks.cancel(endpoint.split("/")[-2], scope=tasks.scope_id(app.lib.root))
        if task is None:
            raise ValueError(tr("找不到这个研究工作。"))
        return task
    if endpoint.startswith("/tasks/") and endpoint.endswith("/ack"):
        task = tasks.acknowledge(endpoint.split("/")[-2], scope=tasks.scope_id(app.lib.root))
        if task is None:
            raise ValueError(tr("找不到这个研究工作。"))
        return task
    if endpoint == "/comparison":
        return comparison.start(app.lib, body.get("paper_ids"), body.get("dimensions"),
                                str(body.get("focus") or ""), str(body.get("model") or ""))
    if endpoint.startswith("/comparisons/") and endpoint.endswith("/export"):
        return comparison_store.export(app.lib.root, endpoint.split("/")[-2])
    if endpoint.startswith("/proposals/") and endpoint.endswith("/title"):
        identity = endpoint.split("/")[-2]
        if comparison_store.get(app.lib.root, identity) is not None:
            return comparison_store.rename(app.lib.root, identity, str(body.get("title") or ""))
        return proposals.rename(identity, str(body.get("title") or ""))
    if endpoint.startswith("/proposals/") and endpoint.endswith("/review"):
        identity = endpoint.split("/")[-2]
        if comparison_store.get(app.lib.root, identity) is not None:
            return comparison_store.decide(app.lib.root, identity, str(body.get("decision") or "") == "accept")
        return proposals.decide(identity, str(body.get("decision") or "") == "accept")
    raise ValueError(tr("找不到这个 Research API 路径。"))

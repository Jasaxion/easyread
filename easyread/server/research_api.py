"""HTTP adapter for the original PaperRead v1/v2 research contracts."""
from __future__ import annotations

import json

from ..app.i18n import tr
from ..app.log import log
from ..chat import smart_annotations
from ..library import planning_store
from ..research import api, tasks, workspace


def matches(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in (api.PREFIX, workspace.PREFIX))


def recover(app) -> None:
    planning_store.recover_interrupted(app.lib.root)
    tasks.recover(app.lib.root)


def statuses(root):
    return planning_store.statuses(root)


def get(app, path: str):
    if not matches(path.split("?", 1)[0]):
        return None
    result = api.get(app, path)
    return workspace.get(app, path) if result is None else result


def post(app, path: str, query: dict, raw: bytes):
    if path == workspace.PREFIX + "/attach-pdf":
        return workspace.attach_pdf(app, query.get("paper_key", ""), raw, query.get("name", "paper.pdf"))
    body = json.loads(raw or b"{}")
    if not isinstance(body, dict):
        raise ValueError("Research request must be an object")
    if path == api.PREFIX or path.startswith(api.PREFIX + "/"):
        return api.post(app, path[len(api.PREFIX):], body)
    from .task_notice_sources import research_request
    endpoint = "/" + path[len(workspace.PREFIX):].strip("/")
    with research_request(app, endpoint, body):
        return workspace.post(app, endpoint, body)


def refresh_annotations(ws) -> None:
    try:
        if (ws.load("reader") or {}).get("ai_annotations"):
            smart_annotations.refresh_after_translation(ws)
    except Exception:
        log.exception("AI annotation source refresh failed %s", ws.id)


def paper_post(app, ws, action: str, body: dict):
    if action == "smart-annotate":
        scope = str(body.get("scope") or "all")
        mode = str(body.get("mode") or "auto")
        intensity = str(body.get("intensity") or "standard")
        if scope not in ("all", "section"):
            raise ValueError(tr("智慧標註範圍不正確"))
        if mode not in ("auto", "suggest", "purpose"):
            raise ValueError(tr("智慧標註模式不正確"))
        if intensity not in ("concise", "standard", "detailed"):
            raise ValueError(tr("智慧標註強度不正確"))
        meta = (ws.load("paper") or {}).get("meta") or {}
        title = str(meta.get("title_zh") or meta.get("title_en") or ws.id)[:300]
        return app.jobs.submit_small(
            "smart-annotate", ws.id, scope=scope, section_id=str(body.get("section_id") or ""),
            mode=mode, intensity=intensity, purpose=str(body.get("purpose") or "")[:500],
            model=str(body.get("model") or ""), title=title,
        )
    if action == "smart-annotate-cancel":
        identity = str(body.get("job") or "")
        if not any(job["id"] == identity for job in app.jobs.small_status(ws.id)):
            return {"cancelled": False}
        return {"cancelled": app.jobs.cancel_small(identity)}
    if action == "smart-annotation":
        return smart_annotations.apply_action(ws, body)
    return None


def trash_count(root) -> int:
    return planning_store.summary_trash_count(root)


def trash_items(root) -> list[dict]:
    return planning_store.summary_trash_items(root)


def trash_post(root, body: dict):
    from ..library import trash

    action, name = body.get("action"), str(body.get("name") or "")
    kind = body.get("type", "paper")
    if action == "move-summary":
        if not planning_store.summary_move_to_trash(root, name):
            raise ValueError(tr("找不到這份已整理摘要"))
        return {"moved": True, "type": "summary"}
    if action not in ("restore", "purge", "empty"):
        raise ValueError(tr("action 只能是 restore / purge / empty"))
    if kind not in ("summary", "paper"):
        raise ValueError(tr("回收站項目類型不正確"))
    if action == "empty":
        return {"deleted": trash.empty(root) + planning_store.summary_trash_empty(root)}
    if kind == "summary":
        if action == "restore":
            return {"restored": planning_store.summary_trash_restore(root, name), "type": kind}
        return {"deleted": planning_store.summary_trash_purge(root, name), "type": kind}
    if action == "restore":
        return {"id": trash.restore(root, name)}
    return {"deleted": trash.purge(root, name)}

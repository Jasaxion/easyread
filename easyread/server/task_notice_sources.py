"""Scalar projections of authoritative producer writes; no producer/file reads."""
from urllib.parse import quote

from ..app.i18n import tr
from .task_notices import ACTIVE, state_label

PAPER_STATES = {"queued": "queued", "running": "running", "confirm": "waiting", "done": "completed",
                "partial": "failed", "error": "failed", "cancelled": "cancelled"}
SESSION_STATES = {"queued": "queued", "running": "running", "waiting_user": "waiting",
                  "waiting_external": "waiting", "paused": "waiting", "idle": "completed",
                  "completed": "completed", "error": "failed", "interrupted": "interrupted"}


def observe(notices, source, identity, data, *, restart=False):
    if source == "sessions":
        # Records.values/items materialize journal history; summaries does not.
        records = data["sessions"]
        for record in records.summaries() if hasattr(records, "summaries") else records.values():
            _session(notices, identity, record)
        return
    if source == "paper-missing":
        return notices.retire("paper:" + identity, order=data.get("_notice_order", 0))
    if source == "paper":
        state = PAPER_STATES.get(data.get("state"))
        if state is None:
            return
        kind = data.get("type") or "prepare"
        stage = state_label(state)
        if state == "running":
            stage = (tr("正在预处理 PDF") if data.get("phase") == "prepare" else
                     tr("正在整理原文") if kind == "read" else
                     tr("正在翻译") if kind == "translate" else stage)
        return notices.publish("paper:" + identity, kind, state, paper_id=identity,
                               url="/read/" + quote(identity, safe=""), stage_label=stage,
                               message=tr("部分内容未完成") if data.get("state") == "partial" else "",
                               done=data.get("done"), total=data.get("total"), restart=restart,
                               order=data.get("_notice_order", 0))
    if source == "small":
        if data.get("kind") not in ("answer", "retranslate"):
            return  # smart-annotate already owns a research task.
        pid = data["pid"]
        return notices.publish("small:" + identity, data["kind"], PAPER_STATES.get(data.get("state")),
                               paper_id=pid, url="/read/" + quote(pid, safe=""))
    if source == "babeldoc":
        state = PAPER_STATES.get(data.get("state"))
        if state is not None:
            return notices.publish("babeldoc:" + identity + ":" + data["job_id"], "babeldoc", state,
                                   paper_id=identity, url="/read/" + quote(identity, safe=""))


def _session(notices, pid, record):
    state = SESSION_STATES.get(record.get("state"))
    if state is None or not record.get("id"):
        return
    stage = state_label(state)
    # Trust runtime state (not awake/active_turn alone): native goals, queued
    # inputs and Pi settling can outlive turn/completed or agent_end.
    if state == "running":
        stage = {"settling": tr("正在收尾"), "retrying": tr("正在重试"),
                 "compacting": tr("正在压缩上下文")}.get(record.get("runtime_phase"), stage)
    elif state == "waiting":
        stage = {"waiting_external": tr("等待外部工作"), "paused": tr("已暂停")}.get(record.get("state"), stage)
    notices.publish("session:" + pid + ":" + record["id"], "workspace-session", state,
                    title=record.get("title", ""), project_id=pid, url="/workspaces?project=" + quote(pid, safe=""),
                    stage_label=stage, only_existing=state not in ACTIVE)


def research_request(app, endpoint, body):
    """Only synchronous AI helpers; async research tasks retain their own notices."""
    from contextlib import nullcontext
    from .task_notices import RequestNotice
    kinds = {"/ai-summary", "/ai-rewrite", "/ai-query", "/ai-category-explain"}
    if endpoint not in kinds:
        return nullcontext()
    paper = body.get("paper")
    if endpoint == "/ai-summary" and isinstance(body.get("papers"), list):
        paper = next(iter(body["papers"]), None)
    title = paper.get("title", "") if isinstance(paper, dict) else ""
    return RequestNotice(app, endpoint.strip("/").replace("/", "-"), title=title)


def candidate_notice(app, item, state, *, accepted=True):
    """Claims already contain metadata; no candidate/library lookup for display."""
    from .task_notices import notify
    return notify(_candidate_notice, app, item, state, accepted=accepted)


def _candidate_notice(app, item, state, *, accepted):
    from .task_notices import notify
    notices = getattr(app, "notices", None)
    if notices is None:
        return
    key = item["paper_key"]
    title = (item.get("paper") or {}).get("title") or key
    notify(notices.publish, "candidate:" + key, "candidate-summary", state if accepted else "interrupted",
           title=title, url="/research", only_existing=not accepted)

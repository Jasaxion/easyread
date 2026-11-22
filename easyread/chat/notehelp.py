"""论文笔记的 AI 助手：点评、帮我改、起草稿。

和「问 AI」对话分开：结果直接流回笔记面板，不进对话记录；由读者决定要不要替换或追加到笔记里。
输出格式和对话一样：一行一个 JSON，{"t": 片段} … 最后 {"done": true} 或 {"error": …}。
"""
from __future__ import annotations

import json
import threading

from ..translate import langs
from ..research.language import output_rule
from ..chat import chat
from ..chat import chat_models
from ..app import config
from ..engines import engines
from ..app.i18n import tr
from ..app.log import log
from ..library.store import Workspace
from ..server.task_notices import RequestNotice

PAPER_BUDGET = 48000  # 不能自己读文件的引擎：随提示词附上的原文与表格字数上限
ANNOTATION_BUDGET = 2500  # 随提示词附上的读者划线与批注字数上限

ASK = {
    "review": ("请点评这份笔记：哪些理解是准确的；哪些地方和论文不符或理解有误（说清论文实际怎么说、在哪一节）；"  # i18n-ok 提示词
               "漏掉了哪些重要内容；还可以往哪想。直接、具体，分条写，不要客套，不要重写整份笔记。"),  # i18n-ok
    "revise": ("请帮读者修改并完善这份笔记：保留他的观点、结构和口吻，修正和论文不符的地方，补上明显漏掉的要点，把表达理顺。"  # i18n-ok
               "善用富文本结构：理顺标题层级，适度补充关键公式（$TeX$）、数据对比表格或待办清单（- [ ]）。"  # i18n-ok
               "若笔记中有未填写的空白区块、空引用块或空列点，请直接根据论文内容补齐具体内容并移除空白项目。"  # i18n-ok
               "只输出修改后的完整笔记（Markdown，公式写 $TeX$），不要解释改了什么。"),  # i18n-ok
    "draft": ("读者还没写笔记。请根据论文内容与读者标注帮他起一份结构严谨、内容充实的高品质读书笔记。"  # i18n-ok
              "所有章节标题与内文一律依照指定语言撰写（保留模型、资料集与指标英文原名）。"  # i18n-ok
              "充分利用富文本排版能力：\n"  # i18n-ok
              "1. 核心问题与动机：阐明背景、核心痛点，并用引用块（> ）总结作者的核心直觉洞见。\n"  # i18n-ok
              "2. 方法与资料流要点：梳理模组分工，写出关键数学公式（行内 $TeX$ 或独立 $$TeX$$）。\n"  # i18n-ok
              "3. 主要结论与关键实验证据：将论文中的关键实验表格数据（如主实验或消融实验）整理成 Markdown 表格，并搭配条列结论。\n"  # i18n-ok
              "4. 局限与延伸思考：指出缺陷或假设条件，并用待办清单（- [ ]）列出后续可追踪或复现的问题。\n"  # i18n-ok
              "使用 Markdown 标题（##）与分层条列组织以利转为心智图。请直接写出具体分析与结论，不要留空白填空区或空列点。只输出笔记本身。"),  # i18n-ok
}


def _table_text(b: dict) -> str:
    cap = b.get("caption_en") or b.get("caption_zh") or ""
    rows = [r for r in (b.get("head") or []) + (b.get("rows") or []) if isinstance(r, list)]
    table_lines = [" | ".join(str(cell).replace("\n", " ").strip() for cell in row) for row in rows]
    return "\n".join(filter(None, [cap, *table_lines]))


def _source_block_text(b: dict) -> str:
    """提取论文区块文字：以精准原文（英文）为主，保留数学公式与表格。"""
    btype = b.get("type")
    if btype == "list":
        return "\n".join(f"- {it.get('en') or it.get('zh', '')}" for it in b.get("items", []) if isinstance(it, dict))
    if btype == "table":
        return _table_text(b)
    if btype == "figure":
        return b.get("caption_en") or b.get("caption_zh") or ""
    if btype == "math":
        tex = b.get("tex", "")
        return f"$${tex}$$" if tex else ""
    return b.get("en") or b.get("zh") or ""


def _paper_text(ws: Workspace) -> str:
    paper = ws.load("paper")
    blocks = paper.get("blocks", [])
    gl = paper.get("glossary", [])
    out, used = [], 0
    if gl:
        gl_text = "已有术语表（专有名词请依此对照）：\n" + "\n".join(f"- {g['en']} = {g['zh']}" for g in gl if g.get("en"))  # i18n-ok 提示词
        out.append(gl_text + "\n")
        used += len(gl_text)
    for b in blocks:
        if b.get("type") == "references":
            break
        btype = b.get("type")
        if btype == "heading":
            t = "\n## " + (b.get("en") or b.get("zh") or "")
        else:
            t = _source_block_text(b)
        if not t:
            continue
        if used + len(t) > PAPER_BUDGET:
            out.append("……（后面的内容省略）")  # i18n-ok 提示词
            break
        out.append(t)
        used += len(t)
    return "\n".join(out)


def _reader_annotations(ws: Workspace) -> str:
    notes = (ws.load("reader").get("notes") or {}).values()
    lines, used = [], 0
    for item in notes:
        if not isinstance(item, dict) or item.get("deleted"):
            continue
        quote = str(item.get("quote") or "").strip()
        body = str(item.get("body") or "").strip()
        if not quote and not body:
            continue
        parts = []
        if quote:
            parts.append(f"划线：「{quote}」")  # i18n-ok 提示词
        if body:
            parts.append(f"批注：{body}")  # i18n-ok 提示词
        entry = "- " + "｜".join(parts)  # i18n-ok 提示词
        if used + len(entry) > ANNOTATION_BUDGET:
            break
        lines.append(entry)
        used += len(entry)
    return "\n".join(lines)


def prompt(ws: Workspace, mode: str, note: str, engine: str, target: str = "") -> str:
    meta = ws.load("paper").get("meta", {})
    title = meta.get("title_zh") or meta.get("title_en") or ""
    paper = ("论文全文在目前目录的 paper.json 里（blocks 里是译文和原文），需要核对时使用 Read 工具读取。"  # i18n-ok 提示词
             if engine == "claude" else "论文原文与关键资料（节录）：\n" + _paper_text(ws))  # i18n-ok
    annotations = _reader_annotations(ws)
    ann_block = f"读者在正文中的划线与批注：\n{annotations}\n\n" if annotations else ""  # i18n-ok 提示词
    return (f"你正在帮读者整理论文《{title}》的笔记。{output_rule(target or langs.reply_code(meta))}{ASK[mode]}\n\n{paper}\n\n"  # i18n-ok
            + ann_block
            + (f"读者的笔记：\n<<<\n{note}\n>>>" if note.strip() else ""))  # i18n-ok


def is_local_model(ecfg: dict, m: dict) -> bool:
    if m.get("engine") != "openai":
        return False
    preset = (m.get("preset") or "").lower()
    base_url = (ecfg.get("openai", {}).get("base_url") or m.get("base_url") or "").lower()
    return preset in ("ollama", "lmstudio") or any(h in base_url for h in ("127.0.0.1", "localhost", "::1", "0.0.0.0"))


def local_model_notice(ecfg: dict, m: dict) -> str:
    if not is_local_model(ecfg, m):
        return ""
    return tr("提示：检测到当前使用本地模型（Ollama / LM Studio），默认上下文通常较小（常为 2k~8k）；若长篇论文未在 Modelfile 调大 num_ctx，后半部实验数据可能受限。")


def handle(handler, ws: Workspace, body: dict) -> None:
    with RequestNotice(getattr(handler, "app", None), "notehelp", paper_id=ws.id) as activity:
        return _handle(handler, ws, body, activity)


def _handle(handler, ws: Workspace, body: dict, activity) -> None:
    """server.py 里 POST /api/p/<id>/notehelp 调这里；handler 是那次请求的 BaseHTTPRequestHandler。"""
    mode = body.get("mode")
    note = str(body.get("note") or "")[:20000]
    if mode not in ASK:
        raise ValueError(tr("mode 只能是 review / revise / draft"))
    if mode != "draft" and not note.strip():
        raise ValueError(tr("笔记还是空的"))
    ecfg, m = chat_models.engine_cfg(config.load(), body.get("model"))
    target = langs.reply_code(ws.load("paper").get("meta"))
    text = prompt(ws, mode, note, ecfg["engine"], target)
    handler.send_response(200)
    handler.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True
    cancel = threading.Event()

    def send(obj):
        handler.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        handler.wfile.flush()
    try:
        init_payload = {"model": chat_models.label(m), "target": target}
        notice = local_model_notice(ecfg, m)
        if notice:
            init_payload["notice"] = notice
        send(init_payload)
        for piece in chat.stream(ecfg, text, ws.root, cancel):
            send({"t": piece})
        send({"done": True})
        activity.finish("completed")
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        activity.finish("cancelled")
        cancel.set()  # 读者点了停止或关了页面
    except engines.Cancelled:
        activity.finish("cancelled")
    except Exception as e:  # noqa: BLE001
        activity.finish("failed")
        log.exception("笔记帮手出错 %s", ws.id)
        try:
            send({"error": str(e)[:500]})
        except OSError:
            pass

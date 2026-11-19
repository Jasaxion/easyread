"""AI 智慧标注：产生可验证的待确认候选，接受后才写入既有 notes。"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import defaultdict

from ..app.i18n import tr
from ..translate import langs
from ..research.language import output_rule
from ..translate.prompts import _block_text
from ..library.store import Workspace, now_iso, text_hash


ANNOTATION_TYPES = {"highlight", "underline"}
COLORS = {"yellow", "green", "blue", "red", "pink"}

HIGHLIGHT_SEMANTICS = {
    "yellow": ("核心主张", "标记完整的重要句子、核心贡献与主要论点（建议 15～90 字）"),  # i18n-ok: prompt
    "green": ("方法与证据", "标记研究方法设计、技术流程与关键实验结果（建议 15～90 字）"),  # i18n-ok: prompt
    "blue": ("背景知识", "标记重要研究背景、问题定义与基本假设（建议 15～90 字）"),  # i18n-ok: prompt
    "red": ("研究限制", "标记研究问题、方法瓶颈与适用限制（建议 15～90 字）"),  # i18n-ok: prompt
}

UNDERLINE_SEMANTICS = {
    "yellow": ("关键词", "标记核心概念、模型名称与专有名词（建议 2～30 字，勿标普通单词）"),  # i18n-ok: prompt
    "green": ("数据指标", "标记具体数值、改善幅度与统计评估指标（建议 2～30 字）"),  # i18n-ok: prompt
    "blue": ("引用来源", "标记重要作者、理论名称、基准模型与文献引用（建议 2～30 字）"),  # i18n-ok: prompt
    "red": ("疑点与风险", "标记不确定性推论、异常现象或潜在矛盾语句（建议 4～35 字）"),  # i18n-ok: prompt
}

INTENSITY_MODES = {
    "concise": {
        "label": "精简",  # i18n-ok: prompt
        "chars_per_item": 500,
        "min_items": 4,
        "max_items": 10,
        "guide": "【精简模式（快速阅读）】每段只保留最有价值的核心资讯（每段约 0～2 处），优先标记最关键的核心主张萤光笔与最重要的关键词／数据底线，宁精勿多。",  # i18n-ok: prompt
    },
    "standard": {
        "label": "标准",  # i18n-ok: prompt
        "chars_per_item": 260,
        "min_items": 8,
        "max_items": 18,
        "guide": "【标准模式（一般研究）】保留各类重要资讯（每段约 1～3 处），均衡运用萤光笔（标记核心主张、方法证据、重要背景、研究限制的完整句子）与底线笔（标记句中关键词、数据指标、引用来源、疑点风险）。",  # i18n-ok: prompt
    },
    "detailed": {
        "label": "详细",  # i18n-ok: prompt
        "chars_per_item": 160,
        "min_items": 14,
        "max_items": 28,
        "guide": "【详细模式（深入研究）】全面保留主次要证据与技术细节（每段约 2～5 处），除完整论点与方法实验句子用萤光笔外，积极使用底线笔标出文中所有重要专有名词、实验数据指标、基准方法／文献引用与假设限制。",  # i18n-ok: prompt
    },
}

MAX_CANDIDATES = 250
CHUNK_CHARS = 4500


def _section_label(block: dict, current: dict | None) -> str:
    if current:
        return " ".join(filter(None, [str(current.get("num") or "").strip(), current.get("zh") or current.get("en") or ""]))
    return tr("论文开头")


def _sources(paper: dict, reader: dict | None = None) -> list[dict]:
    out = []
    current = None
    edits = (reader or {}).get("edits") or {}

    def _edit_zh(key: str) -> str | None:
        entry = edits.get(key)
        # Same rule as reader render.js: a non-null zh is the effective view.
        if isinstance(entry, dict) and entry.get("zh") is not None:
            return str(entry.get("zh") or "").strip()
        return None

    for block in paper.get("blocks", []) or []:
        btype = block.get("type")
        if btype in ("heading", "references"):
            current = block
            continue
        if btype in ("note", "math"):
            continue
        bid = str(block.get("id") or "")
        if not bid:
            continue
        sec_label = _section_label(block, current)
        sec_id = str(current.get("id") or "") if current else ""
        page = block.get("page")
        role = str(block.get("role") or "")
        if btype == "list":
            items = block.get("items")
            if not items:
                items_en = block.get("items_en") or []
                items_zh = block.get("items_zh") or []
                max_len = max(len(items_en), len(items_zh))
                items = [{"en": items_en[i] if i < len(items_en) else "",
                          "zh": items_zh[i] if i < len(items_zh) else ""}
                         for i in range(max_len)]
            for idx, item in enumerate(items or []):
                if isinstance(item, str):
                    item = {"en": item, "zh": item}
                elif not isinstance(item, dict):
                    continue
                key = f"{bid}#{idx}"
                edited = _edit_zh(key)
                zh = edited if edited is not None else str(item.get("zh") or "").strip()
                en = str(item.get("en") or "").strip()
                text = (zh or en).strip()
                if not text:
                    continue
                out.append({
                    "anchor": bid, "key": key, "page": page, "section": sec_label, "section_id": sec_id,
                    "role": role, "zh": zh, "en": en, "sents": None if edited is not None else item.get("sents"), "text": text,
                })
            continue
        if btype in ("table", "figure"):
            key = f"{bid}#caption"
            edited = _edit_zh(key)
            zh = edited if edited is not None else str(block.get("caption_zh") or "").strip()
            en = str(block.get("caption_en") or "").strip()
            text = (zh or en).strip()
            if not text:
                continue
            out.append({
                "anchor": bid, "key": key, "page": page, "section": sec_label, "section_id": sec_id,
                "role": role, "zh": zh, "en": en, "sents": None if edited is not None else block.get("sents"), "text": text,
            })
            continue
        key = bid
        edited = _edit_zh(key)
        zh = edited if edited is not None else str(block.get("zh") or "")
        en = str(block.get("en") or "")
        text = _block_text(block).strip()
        if not text and not zh:
            continue
        out.append({
            "anchor": bid, "key": bid, "page": page, "section": sec_label, "section_id": sec_id,
            "role": role, "zh": str(zh), "en": en,
            "sents": block.get("sents") if btype == "para" and edited is None else None, "text": text or zh,
        })
    return [x for x in out if x["anchor"]]


def _selected_sources(paper_or_sources: dict | list[dict], scope_or_sections: str | list[str] = "all", section_id: str = "", reader: dict | None = None) -> list[dict]:
    if isinstance(paper_or_sources, list):
        sources = paper_or_sources
        if isinstance(scope_or_sections, list):
            target_ids = set(scope_or_sections)
            return [x for x in sources if x.get("section_id") in target_ids or x.get("anchor") in target_ids] or sources
    else:
        sources = _sources(paper_or_sources, reader)
    scope = scope_or_sections if isinstance(scope_or_sections, str) else "all"
    if scope != "section" or not section_id:
        return sources
    by_sec = [x for x in sources if x.get("section_id") == section_id]
    if by_sec:
        return by_sec
    selected = []
    active = False
    section_label = ""
    for item in sources:
        if item["anchor"] == section_id:
            active = True
            section_label = item["section"]
        elif active and item["section"] != section_label:
            break
        if active:
            selected.append(item)
    return selected or [x for x in sources if x["anchor"] == section_id]


def _prompt_source_text(source: dict) -> str:
    """单一语言送入：已翻译优先取译文（zh），未翻译退回原文（en）。"""
    return _clean_prompt_text(source.get("zh") or source.get("en") or "")


def _chunks(sources: list[dict]) -> list[list[dict]]:
    chunks, current, size = [], [], 0
    for source in sources:
        estimate = len(_prompt_source_text(source)) + 120
        if current and size + estimate > CHUNK_CHARS:
            chunks.append(current)
            current, size = [], 0
        current.append(source)
        size += estimate
    if current:
        chunks.append(current)
    return chunks


def _clean_prompt_text(text: str) -> str:
    return str(text or "").replace("‖", "").replace("\u200b", "").strip()


def _paper_overview(paper: dict, sources: list[dict]) -> str:
    abstract_parts = [
        _clean_prompt_text(s.get("zh") or s.get("en") or "")
        for s in sources
        if s.get("role") == "abstract" or "abstract" in str(s.get("section") or "").lower() or "摘要" in str(s.get("section") or "")  # i18n-ok: section match
    ]
    if not abstract_parts and sources:
        abstract_parts = [_clean_prompt_text(sources[0].get("zh") or sources[0].get("en") or "")]
    abstract = " ".join(filter(None, abstract_parts))[:600]
    sec_names = []
    for s in sources:
        sec = str(s.get("section") or "").strip()
        if sec and sec not in sec_names:
            sec_names.append(sec)
    lines = []
    if sec_names:
        lines.append("章节脉络：" + " / ".join(sec_names[:12]))  # i18n-ok: model prompt
    if abstract:
        lines.append("论文核心摘要：" + abstract)  # i18n-ok: model prompt
    return "\n".join(lines)


def _manual_notes_by_anchor(reader: dict | None) -> dict[str, list[dict]]:
    """整理已存在的手动标注（排除 AI 自动产生的标注），供 prompt 避开与后端重叠侦测。"""
    by_anchor: dict[str, list[dict]] = defaultdict(list)
    notes = (reader or {}).get("notes") or {}
    for n in notes.values():
        if not isinstance(n, dict) or n.get("deleted") or n.get("ai_generated") or n.get("ai_annotation_id"):
            continue
        anchor = str(n.get("anchor") or "").strip()
        quote = str(n.get("quote") or "").strip()
        if anchor and quote:
            by_anchor[anchor].append(n)
    return by_anchor


def _detect_manual_overlap(candidate: dict, source: dict, manual_notes: dict[str, list[dict]]) -> None:
    """比对 AI 候选标注与该段手动标注是否有字元区间重叠。"""
    anchor = candidate.get("anchor")
    notes = manual_notes.get(anchor) or []
    if not notes or not candidate.get("locatable"):
        return
    c_side = candidate.get("side") or "zh"
    c_quote = candidate.get("quote") or ""
    c_norm = _norm(c_quote)
    if not c_norm:
        return
    for m in notes:
        m_quote = str(m.get("quote") or "")
        m_norm = _norm(m_quote)
        if not m_norm:
            continue
        # 1. 规范化字串互相包含
        if c_norm in m_norm or m_norm in c_norm:
            candidate["manual_overlap"] = True
            candidate["overlap_quote"] = m_quote[:120]
            candidate["overlap_info"] = {
                "note_id": m.get("id"),
                "color": m.get("color", "yellow"),
                "style": m.get("style", "marker"),
                "quote": m_quote[:120],
            }
            return
        # 2. 正文精确字元区间重叠计算
        text = source.get(c_side) or ""
        if text and c_quote in text and m_quote in text:
            c_start = text.find(c_quote)
            c_end = c_start + len(c_quote)
            m_start = text.find(m_quote)
            m_end = m_start + len(m_quote)
            if max(c_start, m_start) < min(c_end, m_end):
                candidate["manual_overlap"] = True
                candidate["overlap_quote"] = m_quote[:120]
                candidate["overlap_info"] = {
                    "note_id": m.get("id"),
                    "color": m.get("color", "yellow"),
                    "style": m.get("style", "marker"),
                    "quote": m_quote[:120],
                }
                return


def _prompt(title: str, mode: str, purpose: str, chunk: list[dict], overview: str = "", meta: dict | None = None,
            prior_notes: list[str] | None = None, intensity: str = "standard",
            manual_notes_map: dict[str, list[dict]] | None = None) -> str:
    mode_text = {
        "auto": "找出最值得整体标记的句子与词汇，充分发挥萤光笔与底线笔的搭配。",  # i18n-ok: model prompt
        "suggest": "列出可逐笔审阅的候选句子与关键词，结构严谨，宁精勿滥。",  # i18n-ok: model prompt
        "purpose": "挑选与研究目的直接相关、具备研究价值的句子与关键词句。",  # i18n-ok: model prompt
    }.get(mode, "列出高价值候选句子与关键词。")  # i18n-ok: model prompt
    from ..research.language import reply_rule
    lang_rule = reply_rule(meta)
    cfg_mode = INTENSITY_MODES.get(intensity, INTENSITY_MODES["standard"])
    records = []
    total_chars = 0
    for source in chunk:
        text_clean = _prompt_source_text(source)
        total_chars += len(text_clean)
        manual_block = ""
        anchor = source.get("anchor")
        m_list = (manual_notes_map or {}).get(anchor) or []
        if m_list:
            m_quotes = [f'"{_clean_prompt_text(m.get("quote"))}" ({m.get("style", "marker")})' for m in m_list[:3]]
            manual_block = "\n[此段使用者已手动标注，请勿完全重复标记相同文字：" + "；".join(m_quotes) + "]"  # i18n-ok: model prompt
        records.append(
            f"[anchor={source['anchor']} key={source['key']} page={source.get('page') or ''} section={source['section']}]{manual_block}\n"
            f"TEXT: {text_clean}"
        )
    est_items = round(max(total_chars, 800) / cfg_mode["chars_per_item"])
    max_items = max(cfg_mode["min_items"], min(cfg_mode["max_items"], est_items))
    overview_block = f"{overview}\n" if overview else ""
    prior_block = ""
    if prior_notes:
        recent = [str(n).strip() for n in prior_notes[-12:] if str(n).strip()]
        if recent:
            prior_block = "前段已标注之论点摘要（避免重复标注相同观点）：\n- " + "\n- ".join(recent) + "\n\n"  # i18n-ok: model prompt

    hl_rules = "；".join(f"{c}（{info[0]}：{info[1]}）" for c, info in HIGHLIGHT_SEMANTICS.items())  # i18n-ok: model prompt
    ul_rules = "；".join(f"{c}（{info[0]}：{info[1]}）" for c, info in UNDERLINE_SEMANTICS.items())  # i18n-ok: model prompt

    return (
        output_rule(langs.reply_code(meta)) + "你是 PaperRead 的论文阅读标注助手。只能根据提供的文字工作，不可补造论文内容、数据或引用。\n"  # i18n-ok: model prompt
        f"论文：{title}\n{overview_block}研究目的：{purpose or '未指定，依论文研究价值判断'}\n{mode_text}\n"  # i18n-ok: model prompt
        f"{cfg_mode['guide']}\n\n"
        f"{prior_block}"
        "【标注工具与 8 种语意颜色规则（必严格遵循）】：\n"  # i18n-ok: model prompt
        "一、萤光笔（type: \"highlight\"）：标记一段【具有完整语意的连续句子】（建议 15～90 字，不可只标单词）：\n"  # i18n-ok: model prompt
        f"  - {hl_rules}。\n"  # i18n-ok: model prompt
        "二、底线笔（type: \"underline\"）：标记句子中的【特定词汇、数字或关键短语】（建议 2～30 字）：\n"  # i18n-ok: model prompt
        f"  - {ul_rules}。\n"  # i18n-ok: model prompt
        "三、双工具组合：底线笔可标记在已经被萤光笔标记的句子内部之关键词或数据上，形成层次丰富的阅读焦点。\n\n"  # i18n-ok: model prompt
        "【严格品质规范】：\n"  # i18n-ok: model prompt
        "1. 禁止标注章节导览、过渡句或空泛铺陈（例如「本节将介绍…」、「结果如表 1 所示」、「总结而言」）。\n"  # i18n-ok: model prompt
        f"2. 禁止在 note 中单纯重述或换句话抄写原文；note 必须{lang_rule}点出「这句话或词汇的学术角色与价值」（例如：指出方法瓶颈、创新机制、领先幅度或疑点）。\n"  # i18n-ok: model prompt
        "3. 若某段已标记使用者手动标注，严禁标出完全重叠的相同区间，请寻找该段其他价值点或不同层次的词汇。\n"  # i18n-ok: model prompt
        "4. 本批建议输出约 6～" + str(max_items) + " 笔标注，请兼顾萤光笔与底线笔的合理比例。\n\n"  # i18n-ok: model prompt
        "请只输出 JSON，不要 Markdown 或任何说明文字，格式："  # i18n-ok: model prompt
        '{"annotations":[{"anchor":"来源中的 anchor","key":"来源中的 key","quote":"完全照抄对应区块 TEXT 的连续文字",'  # i18n-ok: model prompt
        f'"type":"highlight 或 underline","color":"yellow/green/blue/red","note":"{lang_rule}说明研究角色与价值，一至三句，不要重复原文",'  # i18n-ok: model prompt
        f'"reason":"{lang_rule}简述为何值得标注","confidence":0.85'  # i18n-ok: model prompt
        '}]}\n'  # i18n-ok: model prompt
        f"quote 必须逐字从对应区块的 TEXT 复制，不要翻译、改写、使用省略号（...）或自行补标点。note 与 reason 请{lang_rule}。anchor、key 只可使用来源标签。最多输出 {max_items} 笔。\n\n"  # i18n-ok: model prompt
        + "\n\n".join(records)
    )


def _norm(text: str) -> str:
    # ‖ is an internal sentence-alignment marker and is not rendered as text.
    return re.sub(r"\s+", " ", str(text or "").replace("‖", "").replace("\u200b", "")).strip()


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CITE_BRACKET_RE = re.compile(r"\[\d+(?:\s*[,，–-]\s*\d+)*\]")  # i18n-ok
_SKIP_PUNCT = set(" \t\r\n‖\u200b*_`$，,。.;；:：!！?？()（）[]【】\"'“”‘’「」『』—–-·•")  # i18n-ok


def _normalized_match(text: str, quote: str) -> tuple[int, str] | None:
    """Match whitespace/alignment-marker differences while retaining real offsets."""
    chars, positions = [], []
    previous_space = False
    for index, char in enumerate(str(text or "")):
        if char in ("‖", "\u200b"):
            continue
        if char.isspace():
            if previous_space:
                continue
            chars.append(" ")
            positions.append(index)
            previous_space = True
            continue
        chars.append(char)
        positions.append(index)
        previous_space = False
    target = _norm(quote)
    normalized = "".join(chars)
    start = normalized.find(target)
    if start < 0 or not target:
        return None
    end_index = start + len(target) - 1
    begin = positions[start]
    end = positions[end_index] + 1
    return begin, str(text or "")[begin:end]


def _skeleton(text: str, drop_cites: bool = False) -> tuple[str, list[int]]:
    """Build a punctuation/markdown-insensitive character stream mapped to original indices."""
    raw = str(text or "")
    keep_mask = [True] * len(raw)
    for m in _MD_LINK_RE.finditer(raw):
        # Keep only the link label [label](url), masking out '[' and '](url)'.
        keep_mask[m.start()] = False
        for i in range(m.end(1), m.end()):
            keep_mask[i] = False
    if drop_cites:
        for m in _CITE_BRACKET_RE.finditer(raw):
            for i in range(m.start(), m.end()):
                keep_mask[i] = False
    chars, positions = [], []
    for index, char in enumerate(raw):
        if not keep_mask[index] or char in _SKIP_PUNCT or char.isspace():
            continue
        chars.append(char.lower())
        positions.append(index)
    return "".join(chars), positions


def _expand_formatting_bounds(text: str, begin: int, end: int, quote: str) -> tuple[int, int]:
    """Include balanced markdown/LaTeX markers or trailing sentence punctuation when appropriate."""
    while begin > 0 and text[begin - 1] in ("*", "`", "$") and end < len(text) and text[end] == text[begin - 1]:
        begin -= 1
        end += 1
    q_strip = str(quote or "").rstrip()
    if q_strip and q_strip[-1] in "。.!！？?」”)）" and end < len(text) and text[end] in "。.!！？?」”)）":  # i18n-ok
        end += 1
    return begin, end


def _loose_match(text: str, quote: str) -> tuple[int, str] | None:
    """Fallback matcher for weak models that alter punctuation, spaces, citations, or use ellipsis."""
    raw_text = str(text or "")
    raw_quote = str(quote or "").strip()
    if not raw_text or not raw_quote:
        return None
    # 1. Handle ellipsis ("前半句...后半句" or "前半句…后半句") often produced by smaller models.
    ellipsis_parts = [p.strip() for p in re.split(r"\.{2,}|…+", raw_quote) if len(p.strip()) >= 4]
    if len(ellipsis_parts) >= 2:
        head_hit = _normalized_match(raw_text, ellipsis_parts[0]) or _loose_match(raw_text, ellipsis_parts[0])
        if head_hit:
            tail_offset = head_hit[0] + len(head_hit[1])
            tail_hit = (_normalized_match(raw_text[tail_offset:], ellipsis_parts[-1])
                        or _loose_match(raw_text[tail_offset:], ellipsis_parts[-1]))
            if tail_hit:
                begin = head_hit[0]
                end = tail_offset + tail_hit[0] + len(tail_hit[1])
                if 0 < end - begin <= max(len(raw_quote) * 4, 260):
                    return begin, raw_text[begin:end]
    # 2. Punctuation, whitespace, markdown-syntax, and inline-citation insensitive substring match.
    q_skel, _ = _skeleton(raw_quote, drop_cites=False)
    if len(q_skel) < 4:
        return None
    for drop_cites in (False, True):
        t_skel, t_pos = _skeleton(raw_text, drop_cites=drop_cites)
        q_target = _skeleton(raw_quote, drop_cites=drop_cites)[0] if drop_cites else q_skel
        if len(q_target) < 4:
            continue
        start = t_skel.find(q_target)
        if start >= 0:
            begin = t_pos[start]
            end = t_pos[start + len(q_target) - 1] + 1
            begin, end = _expand_formatting_bounds(raw_text, begin, end, raw_quote)
            return begin, raw_text[begin:end]
    # 3. High-overlap fuzzy sentence alignment (handles 1-2 character typos/paraphrases by weak models).
    if len(q_skel) < 6:
        return None
    from difflib import SequenceMatcher
    t_skel, t_pos = _skeleton(raw_text, drop_cites=True)
    q_target, _ = _skeleton(raw_quote, drop_cites=True)
    if len(t_skel) < 4 or len(q_target) < 6:
        return None
    matcher = SequenceMatcher(None, t_skel, q_target, autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size > 0]
    if not blocks:
        return None
    matched_chars = sum(b.size for b in blocks)
    span_start = blocks[0].a
    span_end = blocks[-1].a + blocks[-1].size
    span_len = span_end - span_start
    # Require >= 82% of quote characters matched and compact span length close to quote length.
    if matched_chars / len(q_target) >= 0.82 and span_len <= int(len(q_target) * 1.25) + 3:
        begin = t_pos[span_start]
        end = t_pos[span_end - 1] + 1
        begin, end = _expand_formatting_bounds(raw_text, begin, end, raw_quote)
        return begin, raw_text[begin:end]
    return None


def _u16_positions(text: str) -> list[int]:
    """Map UTF-16 offsets (the paper.json/JS convention) to Python offsets."""
    positions = [0]
    for index, char in enumerate(str(text or "")):
        positions.append(positions[-1] + (2 if ord(char) > 0xFFFF else 1))
    return positions


def _aligned_zh_location(source: dict, en_location: dict) -> dict | None:
    """Map an English match to its aligned Chinese sentence(s), when available."""
    en, zh, sents = str(source.get("en") or ""), str(source.get("zh") or ""), source.get("sents")
    if not en or not zh or not isinstance(sents, list) or len(sents) < 2:
        return None
    if not isinstance(sents[-1], list) or len(sents[-1]) != 2:
        return None
    en_pos, zh_pos = _u16_positions(en), _u16_positions(zh)
    if sents[-1] != [en_pos[-1], zh_pos[-1]]:
        return None
    en_start = en_pos[min(max(int(en_location.get("index") or 0), 0), len(en))]
    en_end_py = min(len(en), int(en_location.get("index") or 0) + len(str(en_location.get("quote") or "")))
    en_end = en_pos[en_end_py]
    rows = [row for row in sents if isinstance(row, list) and len(row) == 2]
    start_row = next((i for i, row in enumerate(rows) if row[0] > en_start), None)
    end_row = next((i for i, row in enumerate(rows) if row[0] >= en_end), None)
    if start_row is None or end_row is None:
        return None
    zh_start_u16 = 0 if start_row == 0 else rows[start_row - 1][1]
    zh_end_u16 = rows[end_row][1]
    if not (0 <= zh_start_u16 < zh_end_u16 <= zh_pos[-1]):
        return None
    start = next((i for i, value in enumerate(zh_pos) if value >= zh_start_u16), None)
    end = next((i for i, value in enumerate(zh_pos) if value >= zh_end_u16), None)
    if start is None or end is None or end <= start:
        return None
    quote = zh[start:end].strip()
    if not quote:
        return None
    leading = len(zh[start:end]) - len(zh[start:end].lstrip())
    return {"source": source, "side": "zh", "quote": quote, "index": start + leading}


def _aligned_en_location(source: dict, zh_location: dict) -> dict | None:
    """Map a Chinese/target match to its aligned English sentence(s), when available."""
    en, zh, sents = str(source.get("en") or ""), str(source.get("zh") or ""), source.get("sents")
    if not en or not zh or not isinstance(sents, list) or len(sents) < 2:
        return None
    if not isinstance(sents[-1], list) or len(sents[-1]) != 2:
        return None
    en_pos, zh_pos = _u16_positions(en), _u16_positions(zh)
    if sents[-1] != [en_pos[-1], zh_pos[-1]]:
        return None
    zh_start = zh_pos[min(max(int(zh_location.get("index") or 0), 0), len(zh))]
    zh_end_py = min(len(zh), int(zh_location.get("index") or 0) + len(str(zh_location.get("quote") or "")))
    zh_end = zh_pos[zh_end_py]
    rows = [row for row in sents if isinstance(row, list) and len(row) == 2]
    start_row = next((i for i, row in enumerate(rows) if row[1] > zh_start), None)
    end_row = next((i for i, row in enumerate(rows) if row[1] >= zh_end), None)
    if start_row is None or end_row is None:
        return None
    en_start_u16 = 0 if start_row == 0 else rows[start_row - 1][0]
    en_end_u16 = rows[end_row][0]
    if not (0 <= en_start_u16 < en_end_u16 <= en_pos[-1]):
        return None
    start = next((i for i, value in enumerate(en_pos) if value >= en_start_u16), None)
    end = next((i for i, value in enumerate(en_pos) if value >= en_end_u16), None)
    if start is None or end is None or end <= start:
        return None
    quote = en[start:end].strip()
    if not quote:
        return None
    leading = len(en[start:end]) - len(en[start:end].lstrip())
    return {"source": source, "side": "en", "quote": quote, "index": start + leading}


def _locate(quote: str, suggested_anchor: str, sources: list[dict], preferred_side: str = "", suggested_key: str = "", *, allow_loose: bool = True) -> dict | None:
    quote = str(quote or "").strip()
    if not quote:
        return None
    by_key = [x for x in sources if suggested_key and x.get("key") == suggested_key]
    by_anchor = [x for x in sources if x.get("anchor") == suggested_anchor and x not in by_key]
    preferred = by_key + by_anchor
    candidates = preferred + [x for x in sources if x not in preferred]

    sides = [preferred_side] if preferred_side in ("zh", "en") else []
    fallback_sides = [side for side in ("zh", "en") if side not in sides]
    passes = [sides, fallback_sides] if sides else [["zh", "en"]]

    for side_list in passes:
        if not side_list:
            continue
        exact = []
        normalized = []
        for source in candidates:
            for side in side_list:
                text = source.get(side) or ""
                if not text:
                    continue
                if quote in text:
                    exact.append((source, side, text.index(quote), quote))
                else:
                    match = _normalized_match(text, quote)
                    if match:
                        normalized.append((source, side, match[0], match[1], _norm(quote)))
        matches = exact or normalized
        if not matches and allow_loose:
            # Model-output repair is allowed during generation, not source refresh.
            # Try loose matching on preferred block(s) first, then across all blocks.
            loose = []
            for pool in (preferred, [x for x in sources if x not in preferred]):
                for source in pool:
                    for side in side_list:
                        text = source.get(side) or ""
                        match = _loose_match(text, quote) if text else None
                        if match:
                            loose.append((source, side, match[0], match[1], _norm(match[1])))
                if loose:
                    break
            matches = loose

        if not matches:
            continue

        unique = {(m[0].get("key"), m[1], _norm(m[3])) for m in matches}
        if len(unique) != 1:
            preferred_matches = ([m for m in matches if suggested_key and m[0].get("key") == suggested_key]
                                 or [m for m in matches if m[0].get("anchor") == suggested_anchor])
            if preferred_matches:
                if preferred_side:
                    side_matches = [m for m in preferred_matches if m[1] == preferred_side]
                    if side_matches:
                        preferred_matches = side_matches
                matches = [preferred_matches[0]]
            elif len(matches) == 1:
                pass
            else:
                continue

        source, side, index = matches[0][:3]
        actual = matches[0][3]
        text = source.get(side) or ""
        if actual == text and index:
            actual = text[index:index + len(str(quote))]
        return {"source": source, "side": side, "quote": actual, "index": index}

    return None


def _stable_id(paper_id: str, item: dict) -> str:
    raw = "|".join(str(item.get(k) or "") for k in ("anchor", "key", "quote", "type", "color"))
    return "ai-" + hashlib.sha256((paper_id + "|" + raw).encode("utf-8")).hexdigest()[:20]


def _candidate(paper_id: str, raw: dict, sources: list[dict], model: str, purpose: str, created: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    typ = str(raw.get("type") or raw.get("annotation_type") or "highlight").lower()
    if typ not in ANNOTATION_TYPES:
        return None
    color = str(raw.get("color") or "yellow").lower()
    # Existing reader notes call the red semantic color "pink"; keep that
    # canonical value so old CSS and manual annotations continue to render.
    if color == "red":
        color = "pink"
    if color not in COLORS:
        return None
    requested_side = str(raw.get("side") or "").lower()
    raw_quote = str(raw.get("quote") or "").strip()
    raw_quote_en = str(raw.get("quote_en") or "").strip()
    raw_quote_zh = str(raw.get("quote_zh") or "").strip()
    quote_en = raw_quote_en or (raw_quote if requested_side == "en" else "")
    quote_zh = raw_quote_zh or (raw_quote if requested_side == "zh" else "")
    if not quote_en and not quote_zh and raw_quote:
        quote_zh = raw_quote
        quote_en = raw_quote
    if not quote_en and not quote_zh:
        return None
    suggested_anchor = str(raw.get("anchor") or "").strip()
    suggested_key = str(raw.get("key") or "").strip()
    located_zh = _locate(quote_zh, suggested_anchor, sources, "zh", suggested_key) if quote_zh else None
    if located_zh and located_zh.get("side") != "zh":
        located_zh = None
    source = (located_zh["source"] if located_zh else
              next((x for x in sources if suggested_key and x["key"] == suggested_key), None) or
              next((x for x in sources if x["anchor"] == suggested_anchor), None))
    en_anchor = source["anchor"] if source else suggested_anchor
    en_key = source["key"] if source else suggested_key
    located_en = _locate(quote_en, en_anchor, [source] if located_zh else sources, "en", en_key) if quote_en else None
    if located_en and located_en.get("side") != "en":
        located_en = None
    if located_en and not located_zh:
        source = located_en["source"]
    if not source:
        source = {"anchor": suggested_anchor, "key": suggested_key or suggested_anchor, "page": None, "section": "", "zh": "", "en": "", "sents": None}
    if located_zh is None and located_en and source.get("zh"):
        located_zh = _aligned_zh_location(source, located_en)
    if located_en is None and located_zh and source.get("en"):
        located_en = _aligned_en_location(source, located_zh)
    located = located_zh or located_en
    if located and located["source"] is not source:
        source = located["source"]
    actual_quote = located["quote"] if located else (quote_zh or quote_en)
    confidence = raw.get("confidence", 0.0)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.0
    note_text = str(raw.get("note") or "").strip()[:1200]
    reason_text = str(raw.get("reason") or "").strip()[:1200]
    # Weak models sometimes copy the quote verbatim into `note`; replace with `reason` when available.
    if note_text and _norm(note_text) == _norm(actual_quote) and reason_text and _norm(reason_text) != _norm(actual_quote):
        note_text = reason_text
    annotation_side = located["side"] if located else ("zh" if source.get("zh") else "en")
    annotation = {
        "annotation_id": _stable_id(paper_id, {"anchor": source["anchor"], "key": source["key"], "quote": actual_quote, "type": typ, "color": color}),
        "paper_id": paper_id, "quote": actual_quote, "type": typ, "style": "underline" if typ == "underline" else "marker",
        "color": color, "note": note_text, "reason": reason_text,
        "purpose": purpose[:500], "page": source.get("page"), "anchor": source["anchor"], "key": source["key"],
        "prefix": (source.get(annotation_side) or source.get("zh") or source.get("en") or "")[:32], "suffix": "", "confidence": confidence,
        "section": source.get("section") or "", "status": "pending", "locatable": bool(located),
        "side": annotation_side,
        "source_quote_en": located_en["quote"] if located_en else "",
        "source_quote_zh": located_zh["quote"] if located_zh else "",
        "source_en_hash": text_hash(source.get("en") or ""),
        "source_zh_hash": text_hash(source.get("zh") or ""),
        "source_text_hash": text_hash(source.get(annotation_side) or ""),
        "model": model, "created_at": created, "updated_at": created,
    }
    if located:
        text = located["source"].get(located["side"]) or ""
        i = located["index"]
        annotation["prefix"] = text[max(0, i - 32):i]
        annotation["suffix"] = text[i + len(actual_quote):i + len(actual_quote) + 32]
    return annotation


def _dedupe(candidates: list[dict]) -> list[dict]:
    indexed = list(enumerate(candidates))
    # Prioritize locatable candidates and higher confidence so weak-model broken duplicates don't shadow valid ones.
    indexed.sort(key=lambda pair: (
        bool(pair[1].get("locatable")),
        float(pair[1].get("confidence") or 0.0),
        len(pair[1].get("note") or ""),
        -pair[0],
    ), reverse=True)
    kept = []
    seen = set()
    for idx, item in indexed:
        norm_q = _norm(item.get("quote"))
        if not norm_q:
            continue
        key = (item.get("anchor"), item.get("key"), norm_q, item.get("type"))
        if key in seen:
            continue
        seen.add(key)
        overlap = False
        for _, existing in kept:
            if existing.get("anchor") != item.get("anchor") or existing.get("key") != item.get("key"):
                continue
            ex_q = _norm(existing.get("quote"))
            if not ex_q:
                continue
            if norm_q in ex_q or ex_q in norm_q:
                # Allow short underline terms inside a full-sentence highlight if both are locatable.
                shorter, longer = (len(norm_q), len(ex_q)) if len(norm_q) <= len(ex_q) else (len(ex_q), len(norm_q))
                if (existing.get("type") != item.get("type") and item.get("locatable") and existing.get("locatable")
                        and shorter < longer * 0.85):
                    continue
                overlap = True
                break
        if overlap:
            continue
        kept.append((idx, item))
        if len(kept) >= MAX_CANDIDATES:
            break
    kept.sort(key=lambda pair: pair[0])
    return [item for _, item in kept]


def generate(ws: Workspace, *, scope="all", section_id="", mode="auto", purpose="", intensity="standard", model_id="", cancel=None, on_progress=None) -> dict:
    from ..chat import chat_models
    from ..app import config
    from ..engines import engines
    from ..engines import usage
    paper = ws.load("paper") or {}
    reader = ws.load("reader") or {}
    all_sources = _sources(paper, reader)
    sources = _selected_sources(all_sources, scope, section_id)
    if not sources:
        raise ValueError(tr("这篇论文目前没有可供分析的文字。"))
    cfg, selected = chat_models.engine_cfg(config.load(), model_id or None)
    cfg = engines.for_translation(cfg)
    model_name = chat_models.label(selected)
    meter = usage.Meter(cfg.get("engine") or "")
    meta = dict(paper.get("meta") or {})
    meta["target"] = langs.reply_code(meta)
    title = meta.get("title_zh") or meta.get("title_en") or tr("未命名论文")
    overview = _paper_overview(paper, all_sources)
    manual_notes = _manual_notes_by_anchor(reader)
    # 先把旧版本候选重新对照目前 blocks；重新翻译后再按智慧标注时，
    # 使用者会看到哪些项目仍有效、哪些必须重新分析，而不是沿用旧译文位置。
    refresh_after_translation(ws)
    generated = []
    existing = (ws.load("reader") or {}).get("ai_annotations") or {}
    saved = 0
    chunks = _chunks(sources)
    total_chunks = len(chunks)
    prior_notes: list[str] = []

    if on_progress:
        on_progress(0, total_chunks, saved)

    for chunk_idx, chunk in enumerate(chunks, 1):
        if cancel is not None and cancel.is_set():
            raise engines.Cancelled()
        prompt_text = _prompt(title, mode, purpose, chunk, overview=overview, meta=meta,
                              prior_notes=prior_notes, intensity=intensity, manual_notes_map=manual_notes)
        raw = engines.parse_json(engines.run(cfg, prompt_text, ws.root, [], cancel, meter))
        rows = raw.get("annotations", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        candidates = []
        for row in rows:
            cand = _candidate(ws.id, row, sources, model_name, purpose, now_iso())
            if cand:
                cand["target"] = meta["target"]
                src = next((s for s in sources if s.get("key") == cand.get("key") or s.get("anchor") == cand.get("anchor")), {})
                _detect_manual_overlap(cand, src, manual_notes)
                candidates.append(cand)
        chunk_items = _dedupe(candidates)
        ops = []
        for item in chunk_items:
            if any(x["annotation_id"] == item["annotation_id"] for x in generated):
                continue
            generated.append(item)
            note_summary = f"[{item.get('section') or ''}] {item.get('note') or item.get('quote') or ''}"
            prior_notes.append(note_summary.strip())
            old = existing.get(item["annotation_id"])
            if old and old.get("status") in ("accepted", "rejected"):
                continue
            if old:
                item["status"] = old.get("status", "pending")
                item["created_at"] = old.get("created_at", item["created_at"])
            ops.append({"op": "ai_annotation", "annotation": item, "at": item["updated_at"]})
        if ops:
            def unchanged_ops(current):
                annotations = current.get("ai_annotations") or {}
                return [op for op in ops if annotations.get(op["annotation"]["annotation_id"]) ==
                        existing.get(op["annotation"]["annotation_id"])]
            result = ws.update_reader_ops(unchanged_ops, client="smart-annotations")
            saved += len(result["applied"])
            existing = (ws.load("reader") or {}).get("ai_annotations") or {}
        if on_progress:
            on_progress(chunk_idx, total_chunks, saved)
    return {"count": saved, "total": len(generated), "usage": meter.snapshot(), "model": model_name, "target": meta["target"]}


def _valid_patch(annotation: dict, patch: dict, sources: list[dict] | None = None) -> dict:
    out = dict(annotation)
    if "quote" in patch and sources is not None:
        quote = str(patch.get("quote") or "").strip()
        located = _locate(quote, str(annotation.get("anchor") or ""), sources, str(annotation.get("side") or ""), str(annotation.get("key") or ""))
        out["quote"] = quote
        out["source_quote_en"] = out["source_quote_zh"] = ""
        out["locatable"] = bool(located)
        if located:
            source = located["source"]
            out.update({"quote": located["quote"], "anchor": source["anchor"], "key": source["key"],
                        "page": source.get("page"), "section": source.get("section") or "", "side": located["side"]})
            text = source.get(located["side"]) or ""
            i = located["index"]
            out["prefix"] = text[max(0, i - 32):i]
            out["suffix"] = text[i + len(located["quote"]):i + len(located["quote"]) + 32]
            out["source_en_hash"] = text_hash(source.get("en") or "")
            out["source_zh_hash"] = text_hash(source.get("zh") or "")
            out["source_text_hash"] = text_hash(text)
            other = _aligned_en_location(source, located) if located["side"] == "zh" else _aligned_zh_location(source, located)
            out["source_quote_" + located["side"]] = located["quote"]
            if other:
                out["source_quote_" + other["side"]] = other["quote"]
    for key in ("note", "reason", "purpose"):
        if key in patch:
            out[key] = str(patch[key] or "")[:1200]
    if patch.get("color") in COLORS:
        out["color"] = "pink" if patch["color"] == "red" else patch["color"]
    if patch.get("type") in ANNOTATION_TYPES:
        out["type"] = patch["type"]
        out["style"] = "underline" if patch["type"] == "underline" else "marker"
    out["updated_at"] = now_iso()
    return out


def _formal_note(annotation: dict, existing: dict) -> dict:
    nid = "ai-note-" + annotation["annotation_id"].removeprefix("ai-")
    return {
        "id": nid, "anchor": annotation.get("anchor"), "key": annotation.get("key"), "quote": annotation.get("quote"),
        "prefix": annotation.get("prefix", ""), "suffix": annotation.get("suffix", ""), "side": annotation.get("side"),
        "kind": "highlight", "color": annotation.get("color", "yellow"), "style": annotation.get("style", "marker"),
        "body": annotation.get("note", ""), "created": existing.get("created", now_iso()), "updated": now_iso(),
        "ai_generated": True, "ai_annotation_id": annotation["annotation_id"], "stale": False,
    }


def _source_for_annotation(annotation: dict, sources: list[dict]) -> dict | None:
    """Find the current block, even when a retranslation regenerated its block id."""
    anchor = str(annotation.get("anchor") or "")
    key = str(annotation.get("key") or "")
    exact = next((source for source in sources if key and source["key"] == key), None)
    if exact is None and (not key or key == anchor):
        anchored = [source for source in sources if source["anchor"] == anchor]
        exact = anchored[0] if len(anchored) == 1 else None
    hashes = {side: str(annotation.get("source_" + side + "_hash") or "") for side in ("en", "zh")}
    def matches_hash(source, side):
        text = source.get(side) or ""
        return bool(text and hashes[side] and text_hash(text) == hashes[side])
    if exact and (not any(hashes.values()) or any(matches_hash(exact, side) for side in hashes)):
        return exact
    for side in hashes:
        matches = [source for source in sources if matches_hash(source, side)]
        if len(matches) == 1:
            return matches[0]
    return None


def _refresh_annotation(annotation: dict, sources: list[dict]) -> dict:
    """Rebase an AI suggestion against the latest paper blocks after translation."""
    out = dict(annotation)
    source = _source_for_annotation(annotation, sources)
    if not source:
        out.update({"locatable": False, "source_stale": True, "stale_reason": "source_missing"})
        return out
    side = str(annotation.get("side") or "")
    source_quote_en = str(annotation.get("source_quote_en") or (annotation.get("quote") if side == "en" else ""))
    source_quote_zh = str(annotation.get("source_quote_zh") or (annotation.get("quote") if side == "zh" else ""))
    located_en = _locate(source_quote_en, source["anchor"], [source], "en", source["key"], allow_loose=False) if source_quote_en else None
    if located_en and located_en.get("side") != "en":
        located_en = None
    located_zh = _locate(source_quote_zh, source["anchor"], [source], "zh", source["key"], allow_loose=False) if source_quote_zh else None
    if located_zh and located_zh.get("side") != "zh":
        located_zh = None
    if located_zh is None and located_en and source.get("zh"):
        located_zh = _aligned_zh_location(source, located_en)
    if located_en is None and located_zh and source.get("en"):
        located_en = _aligned_en_location(source, located_zh)
    located = located_zh or located_en
    if located:
        out.update({
            "anchor": source["anchor"], "key": source["key"], "page": source.get("page"),
            "section": source.get("section") or "", "source_en_hash": text_hash(source.get("en") or ""),
            "source_zh_hash": text_hash(source.get("zh") or ""),
        })
        text = located["source"].get(located["side"]) or ""
        i = located["index"]
        out.update({
            "quote": located["quote"], "side": located["side"], "locatable": True,
            "source_quote_en": located_en["quote"] if located_en else source_quote_en,
            "source_quote_zh": located["quote"] if located["side"] == "zh" else source_quote_zh,
            "source_text_hash": text_hash(text), "source_stale": False, "stale_reason": "",
            "prefix": text[max(0, i - 32):i], "suffix": text[i + len(located["quote"]):i + len(located["quote"]) + 32],
        })
        return out
    old_zh_hash = annotation.get("source_zh_hash") or (annotation.get("source_text_hash") if side == "zh" else "")
    reason = "translation_changed" if source.get("zh") and old_zh_hash and text_hash(source["zh"]) != old_zh_hash else "source_changed"
    out.update({"locatable": False, "source_stale": True, "stale_reason": reason})
    return out


def refresh_after_translation(ws: Workspace, block_ids: set[str] | None = None) -> dict:
    """Revalidate AI suggestions and accepted notes after paper text is replaced.

    Prefer a verified current translation; fall back to the verified original if
    alignment is unavailable. Quotes without either anchor stay stale for review,
    rather than being fuzzily attached to a different sentence.
    """
    def make_ops(reader):
        annotations = reader.get("ai_annotations") or {}
        if not annotations:
            return []
        sources = _sources(ws.load("paper") or {}, reader)
        scope = set(block_ids or ())
        ops = []
        for aid, annotation in annotations.items():
            if scope and annotation.get("anchor") not in scope and not _source_for_annotation(annotation, sources):
                continue
            updated = _refresh_annotation(annotation, sources)
            changed = any(updated.get(key) != annotation.get(key) for key in (
                "anchor", "key", "page", "section", "quote", "side", "prefix", "suffix", "locatable",
                "source_quote_en", "source_quote_zh", "source_en_hash", "source_zh_hash", "source_text_hash", "source_stale", "stale_reason"))
            if changed:
                updated["updated_at"] = now_iso()
                ops.append({"op": "ai_annotation", "annotation": updated, "at": updated["updated_at"]})
            for note in (reader.get("notes") or {}).values():
                if note.get("ai_annotation_id") != aid or note.get("deleted"):
                    continue
                # Relocation is not a user edit: retaining its timestamp lets a
                # previously queued body edit/deletion still win after this lock.
                note_updated = dict(note, stale=not updated.get("locatable"))
                if updated.get("locatable"):
                    note_updated.update({key: updated.get(key) for key in ("anchor", "key", "quote", "prefix", "suffix", "side")})
                if note_updated != note:
                    ops.append({"op": "note", "note": note_updated, "at": now_iso()})
        if not ops:
            return []
        return ops

    result = ws.update_reader_ops(make_ops, client="smart-annotation-source-refresh")
    return {"updated": len(result["applied"]), **result}


def apply_action(ws: Workspace, body: dict) -> dict:
    action = str(body.get("action") or "")
    accepted_ids = set()
    def make_ops(reader):
        annotations = reader.get("ai_annotations") or {}
        sources = _sources(ws.load("paper") or {}, reader)
        ids = [str(body.get("id"))] if body.get("id") else list(annotations)
        if action == "accept_all":
            ids = [k for k, v in annotations.items() if v.get("status") in ("pending", "edited")]
        elif action == "reject_all":
            ids = [k for k, v in annotations.items() if v.get("status") in ("pending", "edited")]
        elif action == "reject_overlaps":
            ids = [k for k, v in annotations.items() if v.get("status") in ("pending", "edited") and v.get("manual_overlap")]
        if action not in ("accept", "reject", "edit", "accept_all", "reject_all", "reject_overlaps"):
            raise ValueError(tr("智慧标注操作不正确。"))
        ops = []
        for aid in ids:
            annotation = annotations.get(aid)
            if not annotation:
                continue
            if action in ("reject", "reject_all", "reject_overlaps"):
                updated = dict(annotation, status="rejected", updated_at=now_iso())
                ops.append({"op": "ai_annotation", "annotation": updated, "at": updated["updated_at"]})
            elif action == "edit":
                updated = _valid_patch(annotation, body.get("patch") or {}, sources)
                updated["status"] = "edited"
                ops.append({"op": "ai_annotation", "annotation": updated, "at": updated["updated_at"]})
            elif action in ("accept", "accept_all"):
                if annotation.get("status") == "accepted":
                    continue  # Never recreate a deleted note or overwrite a user's edits.
                updated = _refresh_annotation(annotation, sources)
                if not updated.get("locatable"):
                    if action == "accept_all":
                        if updated != annotation:
                            updated["updated_at"] = now_iso()
                            ops.append({"op": "ai_annotation", "annotation": updated, "at": updated["updated_at"]})
                        continue
                    raise ValueError(tr("这笔标注尚未确认文字位置，请先修改原文。"))
                if action == "accept_all" and not body.get("include_overlap", False):
                    for field in ("manual_overlap", "overlap_quote", "overlap_info"):
                        updated.pop(field, None)
                    source = _source_for_annotation(updated, sources)
                    if source:
                        _detect_manual_overlap(updated, source, _manual_notes_by_anchor(reader))
                    if updated.get("manual_overlap"):
                        if updated != annotation:
                            updated["updated_at"] = now_iso()
                            ops.append({"op": "ai_annotation", "annotation": updated, "at": updated["updated_at"]})
                        continue
                updated.update(status="accepted", updated_at=now_iso())
                accepted_ids.add(aid)
                ops.append({"op": "ai_annotation", "annotation": updated, "at": updated["updated_at"]})
                notes = reader.get("notes") or {}
                if not any(n.get("ai_annotation_id") == aid for n in notes.values()):
                    ops.append({"op": "note", "note": _formal_note(updated, {}) , "at": updated["updated_at"]})
        return ops

    result = ws.update_reader_ops(make_ops, client="smart-annotation-review")
    count = len(result["applied"])
    res = {"ok": True, "action": action, "count": count, **result}
    annotations_applied = sum(key.startswith("ai_annotation:") for key in result["applied"])
    if action in ("reject_all", "reject_overlaps"):
        res["rejected"] = annotations_applied
    elif action == "accept_all":
        res["accepted"] = sum("ai_annotation:" + aid in result["applied"] for aid in accepted_ids)
    return res


def sections(ws: Workspace) -> list[dict]:
    paper = ws.load("paper") or {}
    return [{"id": b.get("id"), "label": _section_label(b, b), "page": b.get("page")} for b in paper.get("blocks", []) if b.get("type") in ("heading", "references")]

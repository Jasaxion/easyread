"""Reader/Q&A diagnostics; never relax permissions or replay a model call."""
from ..app.i18n import tr


def sandbox_denied(text):
    text = str(text or "").lower()
    return "bwrap:" in text and any(value in text for value in ("operation not permitted", "permission denied", "no permissions to create a new namespace"))


def sandbox_notice():
    return tr("Codex 工具仍报告 bwrap 权限错误，但论文问答已默认关闭 Codex 内置沙箱。请检查是否运行了旧版本或额外工具自行启用了沙箱；这不代表论文文件缺失，也不需要在问答设置中开启读取权限。问题不会自动重发。")


def failed_file_tool(events):
    # Inspect failed native tool results, not a paper quote or the model's prose.
    for event in events:
        item = event.get("item") or {}
        if not isinstance(item, dict):
            continue
        if (event.get("type") == "item.completed" and item.get("type") == "command_execution"
                and item.get("exit_code") not in (None, 0) and sandbox_denied(item.get("aggregated_output"))):
            return True
    return False

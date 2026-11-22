"""Per-call reading/research permissions; never persisted to model settings."""

READ_TOOLS = "Read,Glob,Grep,WebSearch,WebFetch"
DENIED_TOOLS = "Bash,Write,Edit,MultiEdit,NotebookEdit,Agent,Task"
RESEARCH_PROMPT = (
    "\n\nYou may read and search the paper files and use available web search/fetch tools "
    "when the question needs external sources. Cite source URLs for web claims and "
    "distinguish them from the paper. If a tool is unavailable, say so; never pretend "
    "to have searched. Do not modify files or run commands."
)


def research_prompt(engine: str) -> str:
    if engine == "codex":
        return RESEARCH_PROMPT.replace("Do not modify files or run commands.",
            "Do not modify files or execute project scripts. Use permitted read-only inspection commands when local file reading requires the shell.")
    return RESEARCH_PROMPT


def paper_file_guidance(engine: str) -> str:
    tool = "Read/Grep" if engine == "claude" else "read/grep" if engine == "pi" else "只读检查"
    return (f"论文读取工具已默认提供。需要看全文或节选中省略的内容时，用 {tool} 读取当前目录的 paper.json（blocks 里是译文和原文）；"  # i18n-ok
            "读者的全部标记在 reader.json 的 notes 里。文件或工具不可用时明确说明，不把未读内容说成已核对。\n")  # i18n-ok


def for_chat(cfg: dict) -> dict:
    from .engines import for_translation
    out = for_translation(cfg)
    engine = cfg.get("engine")
    if engine and engine != "openai":
        out[engine] = {**out.get(engine, {}), "_chat": True}
    return out


def opencode_permissions(chat: bool) -> dict:
    permissions = {"*": "deny"}
    if chat:
        permissions.update({tool: "allow" for tool in ("read", "glob", "grep", "list", "websearch", "webfetch")})
    return permissions

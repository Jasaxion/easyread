# CLI capabilities for AI Q&A

[简体中文](qa-cli.md)

In **Settings → Models**, open a Claude Code, Codex or Pi Agent model menu and choose **AI Q&A · CLI capabilities**. The same entry is available in the model editor. The page targets the selected model entry and shows its name. Entries using the same CLI can independently use lean or full capabilities. Save a new model entry before configuring its policy.

## Modes and scope

| Mode | Behavior |
| --- | --- |
| Inherit CLI defaults | Use the shared CLI policy; existing configurations and entries without overrides keep their previous behavior |
| Default lean mode | Provide paper, annotation and attachment reading automatically; trim optional resources |
| Full CLI | Use complete native tools, Skills, MCP and authorized execution permissions without extra CLI approval or mode confirmation |
| Custom | Configure optional resources; baseline paper reading always remains available |

Default settings can read the paper without permission setup. Mode changes apply with **Save Q&A capabilities**, without an additional confirmation. **This is a trusted personal Agent environment:** tools use the service account's permissions. Full mode can write files, execute commands and access networks. Lean mode trims features; it is not OS-level read-only isolation.

**Only new reader AI Q&A and the final answer in Ask the library use these settings.** Translation/retranslation, BabelDOC, keyword expansion, note assistance, smart annotations and research automation do not read the new policy. Existing answers are not reconfigured mid-turn.

Shared defaults remain in `config.json` under `qa_cli.claude` / `qa_cli.codex` / `qa_cli.pi`. Entry overrides live in `qa_model_policies[model-entry ID]` with an engine tag and settings. Inherit removes that override; renaming/changing the model within an entry preserves it, while deletion or changing CLI type prunes incompatible overrides. Stale explicit Q&A IDs require reselection rather than silently using another entry’s permissions.

For a lean translator A and full Q&A model B, assign separate model IDs, set A as default translator with Default lean Q&A, and set B as default question model with Full CLI. Empty IDs both follow the same native default; different card names alone do not select different underlying models. Translation always stays lean, even for an entry whose Q&A policy is full.

Native CLI configuration, credentials and Skill files are not rewritten. CLI installation/resources remain shared: policies and per-skill enablement are independent, not separate OS accounts. Manage this CLI’s defaults affects only entries that inherit them.

## Claude Code

Lean/custom modes always expose and preapprove `Read/Glob/Grep`, even with an empty custom tool list. Lean mode retains `--disable-slash-commands` and strict MCP exclusion to reduce optional resources. Full mode uses `--permission-mode bypassPermissions --tools default`, retaining native resources and configured `extra_args` (such as `--add-dir`) while rejecting protocol changes or resuming another native session. Lean/custom calls do not inherit extra arguments.

Custom tools are entered one per line:

```text
Read
Glob
Grep
Bash(git status *)
```

Built-in names control `--tools` visibility; rules provide `--allowedTools` preapproval. **Preapproval is not an independent security sandbox and does not erase native permissions.** WebSearch, WebFetch and Skill have dedicated toggles. MCP is separate, with native MCP tool permissions retained.

## Codex

Codex does not have Claude's generic `--allowedTools` interface. The UI maps supported native capabilities:

- `shell`: required local file-inspection capability; always enabled.
- `web_search`: Web search/URL reading; there is no invented standalone `web_fetch` configuration.
- `view_image`: required paper-image viewing capability; always enabled.
- `update_plan`: planning tool.

These are capability switches, **not an OS security allowlist**. MCP is separate; custom mode disables additional plugin/app extensions. All user Q&A modes automatically use `--dangerously-bypass-approvals-and-sandbox`, avoiding the container bwrap dependency and interactive approvals. Required `shell/view_image` cannot be disabled; optional resources still follow the mode. Translation and other automatic processing do not use these Q&A permissions.

When custom mode disables MCP, EasyRead uses Codex `app-server config/read` to resolve inherited user/project/configuration-layer servers and disables each for this answer only. This metadata query does not start a model turn. Unsupported effective-config discovery fails closed instead of silently allowing MCP. Verified with Codex 0.169.0.

### Paper-file access in Linux containers

The earlier `bwrap: Failed to make / slave: Operation not permitted` error came from sandbox initialization, not a missing paper path. Codex Q&A now runs without its internal sandbox by default: **no compatibility toggle or repeated authorization is needed**. The old Q&A `sandbox` setting is accepted for compatibility only and no longer controls execution.

Selection questions still contain nearby passages only and long papers retain a context budget. All three engines have baseline tools to read omitted content from `paper.json`, `reader.json` and attachments, without claiming unseen material was verified. If bwrap still appears, check for an old service version or an additional tool starting its own sandbox. Questions are never automatically replayed. The service account still needs OS access to the files; mounts, system permissions and administrator policy are not modified.

## Pi Agent

Use Node.js 22.19 or newer and install/update the latest release through npm, not the curl installer:

```bash
npm install -g --ignore-scripts @earendil-works/pi-coding-agent@latest
pi
# Inside Pi: /login connects a provider; /model selects a model.
```

Choose Pi Agent under **Settings → Models → Add model**. Select from the local Pi catalog or enter a full `provider/model-id`, for example `anthropic/claude-sonnet-4-6` (availability depends on the provider/account). Blank follows Pi's default. Thinking levels are `off/minimal/low/medium/high/xhigh/max`, clamped by Pi to model capabilities. An unauthenticated, missing or uncached catalog can be empty; manual model IDs remain available. Suggestions do not guarantee provider access.

Native configuration lives in `~/.pi/agent/`, overridden by `PI_CODING_AGENT_DIR`. Use Pi's `/settings`, `/model`, or native `settings.json` / `models.json` for defaults, custom providers and compatible endpoints. EasyRead does not expose/rewrite those files or `auth.json` credentials. See [Pi models](https://docs.pi.dev/latest/models) for the format. Rescan after changes; API-key environment variables must be available to the process that starts EasyRead.

- **Default lean:** automatically provides `read/grep/find/ls`; disables extensions, MCP, Skills, context-file discovery, prompt templates and themes to reduce overhead. Explicit model calls still use the network. Translation separately retains its original `read`-only tool selection and lean flags.
- **Custom Q&A:** always retains `read/grep/find/ls`, with optional Skills. Shell, writes, MCP and extensions remain off. An empty custom list cannot disable baseline reading.
- **Full CLI:** preserves native tools, extensions, packages, Skills and MCP, adding `--approve` for this invocation's project resources. No separate `/trust` or confirmation is required. The flag does not persist trust, although loaded extensions/tools can have side effects.
- **Permission boundary:** Pi has **no built-in tool sandbox or per-tool approval prompts**. Read-only tools do not restrict readable paths. Full mode can read/write files, run commands and access networks with the EasyRead process's OS permissions. Neither a project path nor project trust is a security boundary.

Native Skills come from `~/.pi/agent/skills`, `~/.agents/skills` and supported configured paths. Pi itself owns full discovery of package/project resources and symlinks. Q&A imports live in `<EASYREAD_HOME>/qa-skills/pi/` and load with per-invocation `--skill`, without modifying native directories. Custom mode can select visible skills individually. Full mode preserves native discovery: manage native skill switches in Pi; switching to full restores native discovery without a separate confirmation. Managed Q&A Skills remain individually selectable.

Project workspaces also support Pi model/thinking selection, streamed tool events, cancellation, continuation and the native terminal. **Research workspaces always use the full native CLI, with no lean/full selector**, including terminal continuation. Workspace sessions do not read reader Q&A policies: tools, Skills, extensions, MCP and SYSTEM/APPEND prompts follow native configuration. Pi Goal, live steering/approval interaction and automatic EasyRead project MCP injection are not provided; your native MCP configuration remains available.

The full Docker image includes Pi. **CLI & terminal** provides npm install/update, login and authentication status. Existing persistent HOME directories are not overwritten: click **Install** if upgrading an older HOME without Pi. Native-terminal `/login` and `/model` are explicit global actions, distinct from lean Q&A. See [Docker deployment](docker.en.md).

## Paste screenshots into questions

Reader Ask AI, Ask the library and research-workspace Agent conversations accept pasted PNG/JPEG/WebP images, with previews, removal and image-only sending. Each request supports up to 4 images, 8 MB each and 20 MB total; the server validates and reencodes actual image content. Failed sends retain the draft. Workspace image drafts are isolated by project/conversation and held only in memory: send before refreshing. Accepted images remain under the project’s `chat-media/` and are included in project exports. Native Goal does not accept images; use ordinary execution. The selected model must actually support vision; this feature does not make text-only models image-capable.

## Skill import and individual switches

Import a `SKILL.md`, or a ZIP containing one skill at its root or under one enclosing directory. Support folders may include `scripts/`, `references/`, `assets/` and `agents/`.

Required frontmatter:

```markdown
---
name: paper-checklist
description: Review a paper using a reproducibility checklist.
---

Review the problem, method, evidence and reproducibility limits.
```

Limits: 5 MiB upload, 12 MiB expanded, 150 files. Path traversal, links, special files, compression bombs and executable binaries are rejected. Existing names are not overwritten and imports are never executed.

Managed storage:

```text
<EASYREAD_HOME>/qa-skills/claude/<name>/
<EASYREAD_HOME>/qa-skills/codex/<name>/
<EASYREAD_HOME>/qa-skills/pi/<name>/
```

This is outside native CLI discovery roots, so imports do not change translation or other calls.

- **Claude:** a temporary skills-only `easyread-qa` plugin contains the selected imports. It exists only for that Q&A invocation. Request `easyread-qa:paper-checklist` in your question if needed.
- **Codex:** since `codex exec` has no verified extra-skill-directory flag, the Q&A prompt supplies a skill catalog with names, descriptions and paths. The CLI reads the appropriate `SKILL.md`; request `$paper-checklist` explicitly if desired. File inspection is available by default, including in custom mode; no separate shell-enablement step is required to read the Skill.

The manager also lists ordinary direct skills in Claude `~/.claude/skills` (respecting `CLAUDE_CONFIG_DIR`), and Codex `~/.agents/skills` / `CODEX_HOME/skills`. Plugins, system skills and symlinked skills are managed in the native terminal instead.

Individual toggles are Q&A-only overrides. They never rename/delete native skills. **Restore native switches**, then save, clears explicit EasyRead overrides. Per-skill switches do not activate managed skills while the global Skills switch/default lean mode keeps them off.

A terminated import can leave `.import-lock`. Once no import is active, remove only that empty lock directory under the managed engine skill root and retry.

## Native settings and terminal

The subpage displays the native configuration path and links to the existing embedded terminal. Full mode reads native changes for each new answer; refresh the page to update native skill status.

**The controls on this page are Q&A-only. Edits you make yourself to native models, proxies, permissions or skills in the terminal remain native/global operations** and can affect other native calls. EasyRead does not rewrite those files or bypass administrator policy.

Reader Q&A does not wait for interactive CLI approval: baseline reads are provided automatically and full mode uses authorized permissions. OS access and managed restrictions still apply. Claude full mode has a native root guard; retain the full Docker image's default non-root `easyread` user rather than fabricating sandbox markers to bypass it.

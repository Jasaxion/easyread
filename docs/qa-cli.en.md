# CLI capabilities for AI Q&A

[简体中文](qa-cli.md)

In **Settings → Models**, open a Claude Code or Codex model menu and choose **AI Q&A · CLI capabilities**. The same entry is available in the model editor. The page targets the selected model entry and shows its name. Entries using the same Codex/Claude CLI can independently use lean or full capabilities. Save a new model entry before configuring its policy.

## Modes and scope

| Mode | Behavior |
| --- | --- |
| Inherit CLI defaults | Use the shared CLI policy; existing configurations and entries without overrides keep their previous behavior |
| Default lean mode | Explicitly use lean Q&A for this entry, independent of later shared-default changes |
| Full CLI | Remove EasyRead's lean overrides and inherit native settings, skills, MCP, plugins and permissions; never automatically bypass approvals/sandbox |
| Custom | Control skills, MCP, Web capabilities, permitted tools and Codex sandbox separately |

Full mode requires confirmation; changes apply when you choose **Save Q&A capabilities**. Skills/tools can access networks, run commands and write files. Use trusted content only.

**Only new reader AI Q&A and the final answer in Ask the library use these settings.** Translation/retranslation, BabelDOC, keyword expansion, note assistance, smart annotations and research automation do not read the new policy. Existing answers are not reconfigured mid-turn.

Shared defaults remain in `config.json` under `qa_cli.claude` / `qa_cli.codex`. Entry overrides live in `qa_model_policies[model-entry ID]` with an engine tag and settings. Inherit removes that override; renaming/changing the model within an entry preserves it, while deletion or changing CLI type prunes incompatible overrides. Stale explicit Q&A IDs require reselection rather than silently using another entry’s permissions.

For a lean translator A and full Q&A model B, assign separate model IDs, set A as default translator with Default lean Q&A, and set B as default question model with Full CLI. Empty IDs both follow the same native default; different card names alone do not select different underlying models. Translation always stays lean, even for an entry whose Q&A policy is full.

Native CLI configuration, credentials and Skill files are not rewritten. CLI installation/resources remain shared: policies and per-skill enablement are independent, not separate OS accounts. Manage this CLI’s defaults affects only entries that inherit them.

## Claude Code

Default mode retains `--disable-slash-commands`, the existing read-only tool set and strict MCP restrictions. Full mode omits those restrictions and preserves native user/project permissions.

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

- `shell`: shell tools.
- `web_search`: Web search/URL reading; there is no invented standalone `web_fetch` configuration.
- `view_image`: image viewing.
- `update_plan`: planning tool.

These are capability switches, **not a universal security allowlist covering every tool/plugin/MCP surface**. MCP is controlled separately; custom mode disables additional plugin/app extensions. Sandbox choices are read-only, workspace-write or native inheritance. No automatic all-approval/sandbox bypass is offered.

When custom mode disables MCP, EasyRead uses Codex `app-server config/read` to resolve inherited user/project/configuration-layer servers and disables each for this answer only. This metadata query does not start a model turn. Unsupported effective-config discovery fails closed instead of silently allowing MCP. Verified with Codex 0.169.0.

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
```

This is outside native CLI discovery roots, so imports do not change translation or other calls.

- **Claude:** a temporary skills-only `easyread-qa` plugin contains the selected imports. It exists only for that Q&A invocation. Request `easyread-qa:paper-checklist` in your question if needed.
- **Codex:** since `codex exec` has no verified extra-skill-directory flag, the Q&A prompt supplies a skill catalog with names, descriptions and paths. The CLI reads the appropriate `SKILL.md`; request `$paper-checklist` explicitly if desired. When custom mode disables shell, bounded instruction-only content is supplied directly without silently enabling shell access. Skills requiring scripts/files still need suitable permitted tools.

The manager also lists ordinary direct skills in Claude `~/.claude/skills` (respecting `CLAUDE_CONFIG_DIR`), and Codex `~/.agents/skills` / `CODEX_HOME/skills`. Plugins, system skills and symlinked skills are managed in the native terminal instead.

Individual toggles are Q&A-only overrides. They never rename/delete native skills. **Restore native switches**, then save, clears explicit EasyRead overrides. Per-skill switches do not activate managed skills while the global Skills switch/default lean mode keeps them off.

A terminated import can leave `.import-lock`. Once no import is active, remove only that empty lock directory under the managed engine skill root and retry.

## Native settings and terminal

The subpage displays the native configuration path and links to the existing embedded terminal. Full mode reads native changes for each new answer; refresh the page to update native skill status.

**The controls on this page are Q&A-only. Edits you make yourself to native models, proxies, permissions or skills in the terminal remain native/global operations** and can affect other native calls. EasyRead does not rewrite those files or bypass administrator policy.

Headless calls cannot show interactive CLI approval prompts in the reader. Tools requiring approval without an existing allow rule may be denied. Configure appropriate trusted permissions in the CLI rather than treating full mode as a blanket bypass.

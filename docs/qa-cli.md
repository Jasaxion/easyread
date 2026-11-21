# AI 问答的 CLI 功能管理

[English](qa-cli.en.md)

在 **设置 → 模型** 中打开 Claude Code、Codex 或 Pi Agent 卡片菜单，选择 **AI 问答 · CLI 功能管理**。编辑这些模型时也有同名入口。进入的是当前模型条目的独立能力页面，标题会显示模型名称；同一 CLI 的不同条目可以分别选择精简或完整模式。新增模型须先添加保存，再配置能力。

## 三种模式

| 模式 | 行为 |
| --- | --- |
| 继承 CLI 默认 | 沿用该 CLI 的共享默认策略；旧配置和未设置覆盖的条目保持原行为 |
| 默认精简 | 当前条目明确使用原来的问答参数、只读范围和功能限制，不跟随共享默认值的后续变化 |
| 完整 CLI | 去掉 EasyRead 的精简限制，继承 CLI 原生配置、Skills、MCP、插件和权限；不强制开启危险权限绕过 |
| 自定义 | 分别控制 Skills、MCP、Web 能力、允许工具及 Codex 沙箱 |

完整模式会在确认后选中，点击“保存问答能力”才生效。技能和工具可能访问网络、运行命令、读写文件，请只使用可信内容。

**这些设置只作用于新发起的阅读器 AI 问答，以及“全库问 AI”的最终回答。** 翻译、重译、BabelDOC、关键词提取、笔记整理、智能批注和研究工作流等其他自动处理不读取此配置。正在运行的回答不会被中途改权限。

共享默认值仍保存在 `config.json` 的 `qa_cli.claude` / `qa_cli.codex` / `qa_cli.pi`；独立覆盖保存在 `qa_model_policies[模型条目 ID]`，包含 engine 与 settings。选择“继承 CLI 默认”会删除该条目的覆盖。更改型号或名称不影响同一条目的策略；删除条目或切换 CLI 来源时会清理不再匹配的覆盖。失效的明确问答模型 ID 会要求重新选择，不静默改用另一条目的权限。

可将模型 A 指定为默认翻译，并明确选择“默认精简”；模型 B 指定为问 AI 默认，并选择“完整 CLI”。它们应填写不同的实际模型 ID；不同卡片名称不代表不同底层型号，空 ID 都跟随同一 CLI 默认。翻译用途始终保持精简，不因某条目问答模式为完整而改变。

这些设置不会改写 CLI 原生配置或凭据。底层 CLI 安装、原生配置与 Skill 文件仍共享；独立的是条目的问答能力及 Skill 启用策略，而不是隔离的操作系统账户。页面的“管理此 CLI 的默认问答能力”仅影响选择继承的条目。

## Claude Code

- 默认仍使用原来的 `--disable-slash-commands`、只读工具集合和严格 MCP 限制。
- 完整模式不再传这些精简限制，CLI 读取自己的用户/项目设置和权限。
- 自定义允许工具每行一个，例如：

```text
Read
Glob
Grep
Bash(git status *)
```

内置工具名称用于 `--tools` 可见性，权限规则用于 `--allowedTools` 预批准。**预批准不是独立安全沙箱，也不会消除原生权限的影响**。WebSearch、WebFetch、Skill 由各自开关控制；MCP 有独立开关，MCP 工具权限继续使用原生配置。

## Codex

Codex 不提供 Claude 同款的通用 `--allowedTools` 参数。界面使用其真实支持的能力：

- `shell`：Shell 工具。
- `web_search`：网页搜索和 URL 读取使用的 Web 能力，没有虚构一个独立 `web_fetch` 参数。
- `view_image`：查看图片。
- `update_plan`：计划工具。

这些是能力开关，**不是覆盖所有原生工具、插件和 MCP 的安全白名单**。MCP 单独控制；自定义模式会禁用额外插件/应用等扩展。沙箱可选择只读、工作目录写入或继承原生设置。没有提供自动跳过全部审批/沙箱的开关。

自定义模式关闭 MCP 时，通过 Codex 的 `app-server config/read` 读取实际用户/项目/配置层中的 MCP 服务名，然后只为本次问答逐项关闭；不会启动模型回答。若 CLI 版本无法提供有效配置，调用报错而不是悄悄放开 MCP。已在 Codex 0.169.0 验证该接口。

## Pi Agent

使用 Node.js 22.19 或更新版本，通过 npm 安装/更新最新版（不是 curl 安装器）：

```bash
npm install -g --ignore-scripts @earendil-works/pi-coding-agent@latest
pi
# 在 Pi 中运行 /login 连接服务商，/model 选择模型
```

在“设置 → 模型 → 添加模型”选择 Pi Agent。可以从本机 Pi 模型列表选择，也可手填完整 `provider/model-id`，例如 `anthropic/claude-sonnet-4-6`（能否使用取决于服务商和账号）；空值继承 Pi 默认。推理强度支持 `off/minimal/low/medium/high/xhigh/max`，Pi 会按实际模型能力限制级别。未登录、目录缺失或模型未缓存时，列表可能为空，仍可手工配置，不能把候选列表当作服务商可用性保证。

Pi 原生配置位于 `~/.pi/agent/`，可通过 `PI_CODING_AGENT_DIR` 更改。使用 Pi 的 `/settings`、`/model` 或编辑原生 `settings.json` / `models.json` 配置默认模型、自定义服务商与兼容接口；EasyRead 不回显或改写这些文件及 `auth.json` 凭据。原生模型配置格式见 [Pi 模型文档](https://docs.pi.dev/latest/models)。设置保存后重新检测模型；环境变量 API Key 必须对启动 EasyRead 的服务进程可见。

- **默认精简 / 翻译**：只启用读取工具，关闭扩展、MCP、Skills、上下文文件发现、提示词模板与主题；忽略需信任的项目资源，关闭自动联网下载/目录刷新。显式模型请求仍会联网；使用 EasyRead 的任务系统提示，不加载原生 SYSTEM/APPEND 提示文本。
- **自定义问答**：允许选择 `read/grep/find/ls` 并开启 Skills；不开启 Shell、写入、MCP 或扩展。需要这些功能时明确选择完整模式，不会因不支持参数而自动降级放开能力。
- **完整 CLI**：保留原生工具、扩展、包、Skills 与 MCP，沿用 Pi 已保存的项目信任决定，不自动添加 `--approve`。未信任项目的资源可能不会加载；请在 Pi 中自行审核并配置 `/trust`。
- **重要权限边界**：Pi **没有内置工具沙箱，也不会逐次询问工具执行权限**。只读工具选择不限制其读取范围；完整模式可以按 EasyRead 进程的操作系统权限读写文件、执行命令和联网。项目路径和“项目可信”都不是安全隔离边界。

Pi 原生 Skills 来自 `~/.pi/agent/skills`、`~/.agents/skills` 和支持的配置目录；包、项目资源及符号链接的完整发现由原生 Pi 管理。问答导入保存于 `<EASYREAD_HOME>/qa-skills/pi/`，使用本轮 `--skill` 参数加载，不写原生目录。自定义模式可以逐项选择可见 Skills；完整模式保留原生发现，因此原生 Skill 开关在 Pi 中管理，切换完整时会确认清除 EasyRead 的原生逐项覆盖。问答专用 Skills 仍可独立开关。

项目工作区也可选择 Pi Agent，支持模型/推理设置、流式工具事件、停止、续接会话及原生终端；**研究空间始终使用完整原生 CLI，不提供精简模式选择**，包括原生终端续接。项目会话不读取本页阅读问答策略；工具、Skills、扩展、MCP 和 SYSTEM/APPEND 提示均按原生配置加载。当前不提供 Pi 的 Goal、运行中 steering/审批交互，也不自动注入 EasyRead 项目 MCP；可以使用自己原生配置的 MCP。

Docker 完整版内置 Pi，**CLI 与终端**卡片支持 npm 安装/更新、登录与登录状态。旧持久 HOME 不会被镜像覆盖，升级后若没有 Pi，点击“安装”。原生终端中的 `/login` 与 `/model` 是显式全局操作，和精简问答不同。详见 [Docker 说明](docker.md)。

## 粘贴截图提问

阅读页“问 AI”、文献库“全库问 AI”和研究空间 Agent 对话均可直接粘贴 PNG/JPEG/WebP 图片，显示缩略图并可移除；只贴图片也可发送。单次最多 4 张，单张 8 MB、合计 20 MB，服务端检查实际格式并重新编码。失败会保留当前草稿。工作区图片草稿按项目/会话隔离且仅存在内存中，刷新页面前请先发送；已接受的图片保存在项目 `chat-media/` 中，随项目导出。原生 Goal 不支持图像输入，请切换普通执行。模型必须实际支持视觉；程序不能让纯文本模型获得看图能力。

## Skills：导入与逐项开关

可导入：

- 单个 `SKILL.md`；
- 包含一个 Skill 的 ZIP：根目录有 `SKILL.md`，或只有一层外包目录；可带 `scripts/`、`references/`、`assets/`、`agents/`。

必须有 frontmatter：

```markdown
---
name: paper-checklist
description: Review a paper using a reproducibility checklist.
---

Review the problem, method, evidence and reproducibility limits.
```

上传上限 5 MiB，展开上限 12 MiB / 150 个文件。拒绝路径穿越、符号链接、特殊文件、压缩炸弹和二进制可执行文件；不会覆盖同名 Skill，不执行导入内容。问答专用目录：

```text
<EASYREAD_HOME>/qa-skills/claude/<name>/
<EASYREAD_HOME>/qa-skills/codex/<name>/
<EASYREAD_HOME>/qa-skills/pi/<name>/
```

该目录不在 CLI 默认扫描路径内，因此导入不会影响翻译或其他调用。

- **Claude**：本轮临时生成仅含所选 Skills 的 `easyread-qa` 插件，问答结束后删除临时插件。可以在问题中指定使用 `easyread-qa:paper-checklist`。
- **Codex**：`codex exec` 没有已验证的额外 Skill 目录参数，所以通过本轮问答的 Skill 目录提示提供名称、描述和绝对路径，CLI 按需读取 `SKILL.md`。可在问题中使用 `$paper-checklist`。自定义模式未开放 Shell 时，会有限量地直接提供技能说明以支持纯提示词 Skill，不会暗中打开 Shell；需要脚本或其他文件的技能仍受已选工具限制。

页面还展示用户原生目录中的普通 Skills：Claude `~/.claude/skills`（尊重 `CLAUDE_CONFIG_DIR`），Codex `~/.agents/skills` 和 `CODEX_HOME/skills`。不会递归列出插件、系统技能或符号链接，它们仍可在 CLI 终端中管理。

逐项开关只覆盖本次问答的启用状态，不删除/重命名原生 Skill。点击“恢复原生开关”并保存，可清除 EasyRead 的显式覆盖，重新跟随 CLI 设置。全局 Skills 关闭或默认精简模式下，列表中的开关不会使导入技能自动参与问答。

导入进程异常终止可能留下 `.import-lock`；确认没有正在导入后，可在终端删除对应问答技能目录下的空锁目录，再重试。不要删除技能本身。

## 在终端修改 CLI 原生设置

子页面显示原生配置路径，并提供现有内嵌终端入口。完整模式不会忽略用户配置：保存 CLI 原生设置后，新问答会重新读取，页面刷新可更新原生技能状态。

注意区别：**本页的能力开关仅影响问答；你自己在终端修改的原生模型、代理、权限或技能设置仍是原生全局操作**，会按 CLI 自身规则影响其他原生调用。EasyRead 不擅自改写这些文件，也不会自动放宽管理员策略。

Claude/Codex 无头调用无法在网页中显示 CLI 的交互审批。需要交互确认但没有预批准的工具可能被 CLI 拒绝；请在终端按需配置可信工具的原生权限，而不是把“完整模式”理解成绕过全部检查。

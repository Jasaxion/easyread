# SSH 远程研究空间

[研究空间](research-workspaces.md) · [开放 API / MCP](open-api.md) · [English](remote-workspaces.en.md)

远程项目仍使用同一套 EasyRead 界面，但文件、Git、Beads、LaTeX、模型目录、问 AI 和内嵌原生终端在你自己的 SSH 服务器执行。连接失败不会回退到部署机的 Agent。阅读器问答、翻译和全库问答保持原有行为。

## 标准系统与 SSH 配置

后端（包括 Docker 容器）就是普通操作系统环境，使用 **OpenSSH**；终端使用已有的 **ttyd + PTY + WebSocket**。不需要安装 dinotty、额外的终端 sidecar 或另一个 SSH 库。

1. 在研究空间首页打开 **SSH 服务器**，下载环境配置脚本。脚本默认只打印安装指导，审阅后由你在远端执行；`--check` 只做诊断。你也可以直接登录本机/远端终端安装、登录和解决连接问题。
2. 远端准备 Python 3.10+、Git、官方支持版本的 Beads（`bd`）、Dolt、`latexmk`、`pdflatex` 和所选 Codex / Claude Code / Pi。项目需要其他 TeX 引擎、宏包或 BibTeX/Biber 时自行安装。可复用 `scripts/install_workspace_deps.py --install` 安装经校验的 bd/Dolt。
3. 远端安装与部署机**相同源码**的 EasyRead，推荐独立 venv。工作区协议与源码指纹都需匹配，只有版本号相同不够。Agent 登录、Git 身份和网络配置在远端账号中完成。
4. 创建专用、可写的绝对目录，例如 `/home/research/easyread-workspaces`。目录及祖先不能是符号链接。项目位于 `<配置目录>/workspace/<项目ID>`，私有状态在 `<配置目录>/.research-workspaces/<项目ID>`；不要由两个 EasyRead 服务同时管理同一远端工作区。
5. 在 **EasyRead 部署机或容器内**，以运行 EasyRead 的账号配置专用 SSH 密钥、核对远端指纹并准备 `known_hosts`。网页不接收密码或私钥内容；可以指定部署机上的密钥路径或使用该账号的本地 ssh-agent。强制主机密钥检查，不转发 SSH agent。
6. 添加服务器，填写可直接连接的主机名/IP、端口、SSH 用户、远端 workspace、可选私钥路径和远端 Python/venv 路径。当前连接字段是显式参数，不读取 SSH config 别名或 ProxyJump；需要跳板时请先按自己的网络/SSH配置准备可达入口。
7. 选择 Agent 并验证环境，再新建研究项目。创建会重新检查环境，并要求 Git/Beads 初始化成功。绿色检查不保证模型额度、账号权限或论文所需的所有宏包；这些由用户在原生终端中验证。

```bash
# 在远端的相同 EasyRead 源码/运行环境中检查：
/path/to/venv/bin/python -m easyread.workspaces.remote_worker --check \
  --workspace /home/research/easyread-workspaces --engine codex
```

## 远程内嵌终端

- EasyRead 部署进程设置 `EASYREAD_TERMINAL=1`。完整 Compose 默认开启；lite 可显式开启，**部署机无需安装 ttyd 也能代理远程终端**。
- 远端安装 ttyd，并让启动 worker 的远端非交互环境设置 `EASYREAD_TERMINAL=1`。这两个开关分别由用户控制，不把部署机环境或模型密钥自动复制到远端。
- 若远端 SSH 非交互环境不会加载 shell 配置，可自行创建固定的 Python 启动包装脚本，并把服务器配置中的 Python 路径指向它：

```sh
#!/bin/sh
export EASYREAD_TERMINAL=1
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
exec /absolute/path/to/venv/bin/python "$@"
```

在项目中创建/选择 Agent 会话，点 **显示原生终端** 即进入同一原生会话的 TUI。输入、控制键和窗口尺寸由 ttyd/PTY 处理，浏览器只通过 EasyRead 同源地址连接；无需对外暴露远端 ttyd 端口。

**返回图形界面**只隐藏 iframe，终端仍占用会话，不能同时从 GUI 发消息。**停止并关闭终端**终止对应进程并释放占用，保留会话历史。刷新/关闭浏览器或 SSH 断开仍遵循现有 ttyd 生命周期：停止对应终端进程；这不是 dinotty 的持久 VTE/tmux，会话记录可恢复不等于同一 PTY 原地存活。WebSocket 接入不会隐式新建或重启会话。注销访问会话会关闭其终端，不影响其他会话。

## 全库 API / MCP：使用现有完整授权码

文献关联只保存 ID，远端项目的 `.easyread-papers.json` 记录这些引用，**不再复制正文快照或引入项目级文献权限**。用户在远端 CLI 配置现有开放 API/MCP，即可实时读取整个文献库，包括未关联论文、最新笔记和批注；无需重新关联或刷新快照。

研究空间的 **文献 API 接入** 按钮提供配置示例，不读取或保存真实令牌。按[开放 API](open-api.md)在 EasyRead 后端启用：

```yaml
# docker-compose.yml 的 environment，或在 .env 中设置对应变量
EASYREAD_OPEN_API: "1"
EASYREAD_MCP: "1"
EASYREAD_PUBLIC_URL: "https://your-easyread.example.com"
```

重启后，在**远端服务账号的原生 CLI 配置/环境变量**中设置 URL 和完整凭据。`Authorization: Bearer <设置 → 访问保护中的现有授权码>` 就是现有完整 API 凭据，不新增受限令牌；它保留现有开放 API 的全部权限，包括全库读取和导入。网页 `X-Token` 是另一套浏览器 CSRF 机制，不要混用。既有 API 的能力范围不变，不会因此新增系统设置或终端管理接口。

```bash
export EASYREAD_BASE='https://your-easyread.example.com'
# 在自己的终端安全设置 EASYREAD_CODE，不要把真实值写入项目/Git。
curl --fail -H "Authorization: Bearer $EASYREAD_CODE" "$EASYREAD_BASE/api/v1/info"
curl --fail -H "Authorization: Bearer $EASYREAD_CODE" "$EASYREAD_BASE/api/v1/documents?limit=10"
codex mcp add easyread --url "$EASYREAD_BASE/mcp" --bearer-token-env-var EASYREAD_CODE
```

Claude Code 可使用[标准 HTTP MCP JSON 配置](open-api.md#mcp-连接)，Pi 沿用用户安装的 MCP 扩展或 API Skill。应配置远端账号的原生 CLI HOME，重新打开会话读取；EasyRead 不替换这些原生配置，也不再为远程 Codex/Claude 自动注入只读快照 MCP。未配置时请按界面提醒处理，不会把旧快照伪装成最新文献。

服务地址必须能从远端访问。远端的 `localhost` 指远端自己；可使用部署地址、VPN 或用户自行建立的标准 SSH 端口转发。网络访问建议 HTTPS，Host 白名单和访问授权沿用现有规则。旧版本产生的快照文件会保留，分享/备份时自行检查；不会主动删除用户数据。

## Docker 与数据边界

完整与 lite 镜像后端均包含 OpenSSH 客户端；完整镜像已有本地 ttyd，远程使用远端 ttyd。Compose 暴露 API/MCP/终端开关，但不会自动更改用户的访问保护。密钥和 `known_hosts` 需对容器服务账号可见；可在 CLI HOME 卷中配置专用文件，不必挂载整个个人 HOME。

SSH 账号和 Agent 有该账号的操作系统权限，项目目录不是沙箱。本功能针对可信个人服务器；用户可以在远端保存完整 EasyRead API 授权码。应用不把这些原生 CLI 凭据写入研究文件或导出包；用户/Agent 自行写入文件的秘密仍可能被导出。

本地 `<文献库>/.remote-workspaces/registry.json` 保存 SSH 配置及离线项目引用。项目文件和原生历史在远端；两边分别备份。导出取自远端，导入仍创建新的本地项目，不接受归档提供的执行目标。

## 连接与验证

同一服务器按需复用 SSH；空闲回收前检查 Agent、目标、编译和终端占用。主动断开要求先停止活动任务，成功后暂停自动重连，需明确点击 **重新连接**。隐藏页面暂停主轮询，失败时退避；输入和写入不自动重发。若响应丢失，先查看远端状态再重试。

```bash
python -m unittest discover -s tests -p 'test_workspace_remote*.py' -v
node --test tests/web/test_workspace_servers.cjs tests/web/test_workspace_library_access.cjs
# Linux，临时 sshd/密钥/目录；不操作用户服务器：
EASYREAD_TEST_SSH=1 python -m unittest tests.test_workspace_ssh_integration -v
```

真实 SSH/ttyd 测试使用交互式测试 Agent，覆盖终端输入、缩放、鉴权、GUI/TUI 互斥、关闭、进程回收及完整 API 读取。它不代替真实提供商登录或论文编译验收。

实现参考 [dinotty](https://github.com/xichan96/dinotty)（MIT，参考版本 `e701cc6c0dedf83a4ff32f8ae4ea95889059c552`）的终端创建/接入分离、会话归属及流式输入输出。未复制或打包其 Rust/Vue 代码；复用 EasyRead 已有 ttyd 和 OpenSSH。

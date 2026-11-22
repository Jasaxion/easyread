# 研究空间

[使用指南](guide.md) · [English](research-workspaces.en.md)

**研究空间**（`/workspaces`）独立于原有的[研究探索与论文规划](research.md)。每个项目集中管理文件、文献引用、原生 Codex/Claude 会话、Git/Beads 和服务端 LaTeX 编辑器。阅读、翻译和离线 HTML 导出的行为不变。研究空间需要连接运行中的 EasyRead 服务，不是离线 PWA 编辑器。

远程运行见 [SSH 远程研究空间](remote-workspaces.md)：服务器管理、环境校验和同一工作区界面，文件与问 AI 均在远端执行。下文未特别说明的路径和宿主权限描述适用于本地项目。

## 开启前先了解权限

这是供**可信个人使用的私有开发工作区**，不是多租户托管服务。发送 Agent 消息或打开原生终端即明确启动具有完全权限、原生工具、Skills 和 MCP 的 CLI。Agent 可以执行命令、修改文件、访问网络，并使用 EasyRead 服务账号可见的凭据。项目目录只是工作目录，**不是操作系统沙箱**。原有论文阅读与翻译的工具策略不变。

建议使用专门的系统/容器账号，保留 Docker 默认的本机端口绑定；需要网络访问时先设置访问授权码并使用 HTTPS。不要挂载整个个人 HOME、SSH 目录或 Docker socket。导入后先检查内容，再决定是否信任。仅导入或打开项目不得执行导入的 Git hooks、Beads 配置、Agent 启动指令或 TeX。显式启动 Agent 前请检查项目配置。**用户或 Agent 主动写入项目文件的密钥**可能进入项目导出，分享前必须检查。

## 从第一个项目开始

1. 在文献库/研究导航附近打开**研究空间**，填写项目名称、简介和默认 Codex 或 Claude 引擎。可按名称/简介搜索，随时修改展示信息，删除时需确认。
2. 项目存放在 `<文献库根目录>/workspace/<随机ID>`。随机小写英文目录名创建后不变，修改展示名称不会移动目录。新项目初始化 `README.md`、`AGENTS.md` 或 `CLAUDE.md`，并在依赖可用时初始化 Git 和 Beads。缺失工具会显示能力错误，不会把空目录伪装成已初始化仓库；请安装后重试对应操作。
3. 文件树包含隐藏文件和配置文件，支持新建文件/文件夹、上传、重命名、移动和删除。Markdown、图片和 PDF 可预览，文本可编辑。文件树与关联文献可独立折叠。未选择文件时 AI 占满剩余区域；关闭文件预览后恢复。拖动分隔条可调整宽度。
4. Agent 或 Git 的外部修改通过轮询同步。未保存的编辑内容不会被自动覆盖。保存冲突时，先比较，再重新加载最新版本或另存副本；不要仅为清除提示而丢掉草稿。

**低开销访问：** 当前页面保持原有刷新频率；应用内非活动分页暂停主界面轮询，切回时恢复刷新。自动保存、服务端 Agent、原生终端和编译任务不会因此停止。未变化的文件、目录和编译状态可只返回小型确认响应，数据变化时仍读取完整内容；侧栏不再反复请求未关联论文，候选搜索仍在“关联论文”中正常提供。打包 JS/CSS 等资源支持有界 gzip 缓存，已有 ETag 校验保留，不缓存私有 API 或压缩 PDF/流式响应。Windows 文件读取保留保守的完整读取路径，其余优化不变。

文件保存会把被替换的磁盘版本与提交草稿保留到 `.easyread-revisions/`，便于恢复 Agent 并发写入；它们也包含在项目导出中。保存不是跨进程事务式 CAS：路径可能短暂不存在，但不会通过覆盖方式丢弃竞争写入。恢复目录达到 300 项或 200 MiB 时会暂停保存，确认不再需要后通过文件树清理。恢复副本本身不可直接覆盖，可另存到普通路径。发布完整文件要求文件系统支持硬链接。Git 管理操作同样保留已观察到的配置冲突，原生外部进程对临时清理文件的延迟写入不在保证范围内；操作前停止其他 Git/Agent 写入。

项目列表将最近更新时间超过 7 天的项目放入默认折叠的「7 天前的项目」，搜索仍覆盖旧项目。研究空间在多标签模式中使用独立标签；「＋」和授权后的默认入口仍是文献库。

普通文件编辑器的齿轮「编辑器设置」提供 10–22px 字号滑杆（默认 13px），即时生效并按项目/设备记忆，不重建编辑器、不更改草稿或撤销记录；原有「跳转到行」收在这里。LaTeX 仍通过其顶部设置调整自己的字号，图片/PDF 预览不受源码字号影响。编辑器默认自动保存，也可以明确关闭。问 AI 支持输入 `@` 选择项目文件，以可移除的引用标签提交；文件内容由原生 CLI 按需读取。回答中的当前项目文件链接可直接打开预览，工具活动默认折叠在各自的问答轮次内，不再保留全局活动列表；成功完成的轮次不会因中途工具重试而一直标红。发送使用 Enter，换行使用 Shift+Enter（兼容 Ctrl/⌘+Enter 发送）。

### 阅读优先的界面

问答与终端共用一条紧凑标题栏，健康会话不再常驻 Pi 权限/能力说明。模型、原生 ID 等低频信息放在会话菜单；文献 API 接入放在项目菜单和关联对话框。自动保存与字号收在编辑器齿轮中，保存和源码/预览仍可直接操作。真正的错误、待回复请求、运行任务与停止按钮保留，长恢复说明可点“详情”查看。

手机默认收起文件/文献导航，点击顶部对应图标打开浮层，选择文件后自动收起。打开文件后，通过顶部的“项目文件 / 问 AI（或原生终端、PDF）”按钮聚焦一个区域，不再同时堆叠两个半屏面板；切换仅改变显示，保留编辑器、草稿及已打开的终端进程。桌面保留原有分栏。终端自动跟随 EasyRead 的明暗模式，不再单独选择配色。

手机终端支持纵向滑动，借鉴 [dinotty](https://github.com/xichan96/dinotty) 的 touch-to-wheel 思路，交给 xterm 处理历史滚动/全屏 TUI。实现没有复制其代码或引入新服务。手势阈值、取消、选择、输入法、多指操作和清理边界已通过本地 Chrome 触摸模拟及真实 ttyd/SSH 验证；真实 iOS/Android 输入法与选择手柄仍需设备反馈。

### 文献与对话

本地项目搜索现有文献库并**关联**论文。关联保留原文献 ID，不把 PDF、笔记和批注复制到项目内；解除关联不删除原论文。单击在点击处附近显示精简浮层，只提供基础信息、开始阅读和复制引用；双击直接阅读。项目 MCP 支持 Agent 搜索/读取文献、笔记与批注，以及建立/解除关联。项目导出不包含关联文献的原始数据；跨设备迁移时请另外保留文献库。

每个项目可有多个 Codex 和 Claude 会话，分别选择原生引擎，并切换、重命名、取消或删除。新建会话的模型字段支持搜索式下拉、刷新和自定义型号；“跟随 CLI 默认”保留默认策略；恢复已有 Codex 会话时会解析并应用当前原生默认，避免旧轮次的模型/强度覆盖当前配置。Codex 优先使用显式模型目录或缓存，缺失时使用有时间/输出上限的原生 `model/list` 元数据查询；Claude 提供别名、配置与已有型号记录，不额外发起问答探测。候选列表不代表账号或代理保证支持，刷新不会修改已有选择。新建或「会话操作 → 模型与思考强度」可设置当前会话的模型及思考强度，空值跟随 CLI 默认。Codex 优先使用模型公布的档位（包括受支持的 max/ultra），Claude 使用自身的 low/medium/high/xhigh/max，不在引擎间静默映射。实际支持以原生 CLI 和模型为准；正在运行或存在待处理工作时不能修改设置。刷新、切换文件、进入 LaTeX 编辑器不应创建新的原生对话。原生会话 ID 和项目拥有的会话状态持续保存，CLI 登录与个人配置仍在服务账号自己的 CLI HOME 中。终端能力可用时才显示原生终端入口；同一原生会话不能同时在聊天与终端运行。完整 Docker 包含 `ttyd`；源码/桌面缺少它时仍可使用聊天。

「显示原生终端」现在直接切换问 AI 面板，再点击「返回图形界面」只隐藏终端，不断开连接或停止任务。GUI 可以查看同步的原生历史，但终端占用该会话时不能同时发送；使用带确认的「停止并关闭终端」释放占用后再继续。切换到其他 session 也会保留已打开终端，结束/删除的终端才清理；仍受服务端原生终端数量限制。GUI 与 TUI 沿用同一原生会话 ID，不是同一个运行进程的两种完全等价视图。**刷新或关闭浏览器仍会断开原生终端并结束对应进程**；需要离开浏览器的长程工作请使用普通执行/原生目标，这次没有改变 ttyd 的断线生命周期。

### Pi 会话兼容与故障恢复

Pi 原生记录不仅有聊天消息，也包含 `bashExecution` 等命令记录。命令记录使用 `command/output`，不要求 `content`；系统、工具、自定义和摘要记录保留在原生文件中，不伪装成聊天正文。导出保留原始记录，导入仅调整原生头部的结构性目录/身份字段，移除不可移植的 fork 来源，不改写历史命令、输出或签名内容。

文件、文献与会话分别加载。一个会话读取失败不会阻止文件树、编辑、预览或 LaTeX 入口，也不会被当成 SSH 断线。真正损坏或正在写入的尾记录会在对应会话显示“记录待核对”；旧历史、输入草稿和原生 ID 保留。使用 **重新读取会话** 只重试读取，不删除文件、不重发指令，也不自动新建对话。校验恢复后清除该条警告；其他已有错误不会被顺手清除。

未解决的警告会阻止该会话的新消息和新终端启动，但已打开终端及停止控制仍可使用。已停止的坏会话不会阻塞健康会话；仍在运行且身份未知的写入方继续保守阻止同引擎并发。部分 JSON/UTF-8 尾记录可先展示已验证的完整前缀，严格导入与续聊仍要求记录完整。不要为消除提示而删除原生会话文件。

### 长程执行、提问与资源回收

「普通执行」同样支持长程指令、Skills 和插件，不需要先开启 Goal。Codex 通过服务端 App Server 执行；浏览器仅查看状态，刷新、切换 session 或关闭页面不会停止已提交的工作。显式的 `$skill-name` 前缀会按当前项目的原生 Skills 清单解析，其他内容保持普通提示文本。当前配置或安装版本不支持的能力会明确报错，不会悄悄换一个新对话或模拟“继续”循环。

「原生目标」是可选功能：输入明确目标或 `/goal <目标>` 后由 Codex 自身决定后续轮次，可暂停、恢复或清除。普通指令/Skill 何时结束仍由原生执行决定，不自动改成 Goal；一次回复结束也不等于整个研究目标已完成。运行中可用「调整当前轮」补充指导，旧轮次已结束时保留草稿并提示重新核对。

原生工具发出问题、审批或 MCP 交互请求时，会在对应会话显示待回复卡片。必须回答的请求显示等待状态；关闭页面不丢失问题，也不会自动猜测答案或授予权限。回复绑定原生请求 ID 和运行实例，服务/原生进程重启后旧请求会标为过期，不能把旧答案直接重放到新实例。Codex 默认模式的问题工具还取决于原生 `default_mode_request_user_input` 等能力配置；MCP 的复杂表单或设备验证流程暂不完整支持，可拒绝而不是自动接受。

会话历史保留在磁盘；列表只加载摘要，选中会话加载有限的近期内容，较早消息按需读取。当前实现按会话懒加载原生运行实例，并设置全服务上限：不是为全部历史 session 永久保留 CLI。空闲回收前检查当前轮次、Goal、原生队列、后台工具、子任务和待回复请求；等待实验、等待用户或状态不确定不视为空闲。

| 服务环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `EASYREAD_WORKSPACE_MAX_ACTIVE` | `4` | 活跃工作会话上限，包含现有 Claude 执行 |
| `EASYREAD_WORKSPACE_MAX_RUNTIMES` | `8` | 已加载 Codex 运行实例上限 |
| `EASYREAD_WORKSPACE_IDLE_GRACE` | `300` | 确认空闲后的回收宽限，单位秒 |
| `EASYREAD_WORKSPACE_TURN_TIMEOUT` | `0` | 仅工作台 Claude exec 的可选超时；0 不设固定时限，正数为秒，不再限制为最多一天 |

普通 Codex 执行和原生 Goal 均不再套用旧的一小时/二十四小时计时器；网络 RPC 的短超时不等同于任务总时限。达到容量上限会返回明确的忙碌提示（HTTP 429），本轮**没有跨会话持久排队或自动重提**。可以暂停/停止其他工作或等待空闲回收后再提交。停止会话会保留历史；不需要为释放资源删除会话。缺乏原生活动状态证据时保守保留运行资源，必要时请明确停止。原生输入/事件队列各限制为 8 MiB 序列化载荷，待处理请求合计限制为 1 MiB；这些限制不等同于进程 RSS 或 Agent 自行启动的实验程序内存上限。

不同 session 仍共享项目文件目录，并非文件系统隔离。并行写代码请使用独立项目/worktree，实验请使用独立输出目录；本轮不自动创建 worktree，也不实现集群作业调度。建议保存实验作业 ID、日志和 checkpoint，避免恢复时重复启动同一实验。

**试用与升级：** 审核时使用文献库副本。正式切换前备份完整元数据并停止旧服务，不要让旧 JSON 版本与新 journal 版本并行写同一项目；保留的旧 state.json 不包含升级后的消息，不能当作直接回滚文件。

**恢复边界：** EasyRead 后端或原生运行实例退出会中断工作；重新打开时保留会话并提示核对已有结果，再由用户明确继续。历史和 Goal 状态不代表被杀掉的实验进程能原地复活。尚未实现独立于 Web 服务生命周期的外部 runner。Claude 仍走原有 exec 路径，可以执行长程 Skills，但不宣称已具备 Codex Goal 或双向提问能力。

### Git、Beads、导入导出

新项目执行官方 `bd init` 时，Beads 会自动建立一个本地 Git 初始化提交（`bd init: initialize beads issue tracking`），记录初始化文件；这是上游工具的引导行为，不会推送。此后的 Git 提交、远程地址、拉取和推送均由用户显式操作，不提供强制推送。请为服务账号设置 Git 身份与远程凭据，不要把令牌放在远程 URL 或项目导出中。导入的 hooks/config 不会因为来自 ZIP 就获得信任。

Beads（`bd`）在项目内保存任务依赖和 Agent 工作记录。当前固定的官方 **1.3.4** 发行二进制默认支持**嵌入式 Dolt**，无需共享 SQL 服务或暴露数据库端口。完整 Docker 同时安装 Dolt **2.7.5**，供原生命令/维护使用。某些上游 Go 构建仅支持服务器模式，建议使用官方支持嵌入模式的发行包。JSONL 导出不是完整的 Beads 数据库，Git push 也不能保证所有原生 Beads 状态已备份。

通过项目操作导出/导入整个项目，包括项目文件、Git/Beads、元数据、完整对话/事件和项目拥有的原生持久状态。v2 会话归档包括子会话、任务、文件历史、记忆及受支持的 Codex SQLite 数据库；迁移到新项目时保留原生会话 UUID、更新工作目录和数据库索引。不导出宿主认证/配置、环境快照、运行锁、诊断日志、可重建全局缓存或外部服务状态。遇到未知原生布局/schema 会明确停止导出，需升级对应适配器，不静默漏掉任务状态。普通删除不是对 Git/原生数据库历史的安全擦除。导入项目使用独立身份，导入时不执行内容。灾难恢复请同时备份完整文献库/数据与**单独安全保存的 CLI HOME**，不要公开 CLI HOME 备份。删除项目或做文件系统备份前，请停止正在运行的 Agent 和编译。

## Beads 项目管理

打开研究项目后，点击顶部 **LaTeX 编辑器左侧的看板图标**，即可打开悬浮的 **Beads 项目管理** 面板。在项目视图和 LaTeX 视图中都可使用，不会切换编辑器或启动 Agent。面板标题显示当前项目名称，仅访问这个项目的 `.beads`，没有跨项目目录选择器。

- **看板 / 列表**：按待处理、进行中、受阻、已推迟、已完成组织任务；可搜索标题、描述、ID、负责人和标签，并按状态、类型、优先级筛选。就绪筛选遵循原生 `bd ready`，不会把所有待处理任务误判为就绪。依赖阻塞的任务显示在受阻栏。
- **新建 / 编辑**：设置标题、描述、类型、P0–P4 优先级、负责人和标签；点击任务打开右侧详情，通过状态选择和保存完成关闭、重新打开等操作。保存成功后才更新界面，不自动重试写入。
- **协作**：查看依赖、父子关系、评论，添加或移除前置阻塞依赖，记录进展评论。原生 Agent 与 GUI 使用同一个 Beads 数据库，不另建任务存储。
- **刷新与草稿**：仅面板打开且页面可见时约每 10 秒读取任务，也可手动刷新。刷新不会替换任务表单；发现任务已被外部修改会提示重新载入。关闭或切换前会确认未保存输入。完成列表更新后可再次打开任务查看最新评论。
- **边界**：不提供永久删除、跨项目汇总、Git/Dolt 推送或自动初始化。CLI 缺失、未初始化或不受支持的数据库配置显示错误，不把失败伪装成空看板。窄屏在列表与任务详情间切换，支持键盘操作和深浅主题。

**兼容与协作限制：** 面板支持官方 bd 1.3.1–1.3.4 的项目内嵌入式数据库；服务端推荐固定的 1.3.4。自定义 YAML、重定向、共享服务器和自定义 Git 执行配置会被明确拒绝，不自动改写。CLI 升级后请先备份、通过原生 bd 处理项目升级，再使用 GUI 写入。关闭任务仍受原生阻塞和负责人规则限制；GUI 不会强制夺取 Agent 的任务。保存前的版本检查可发现已发生的修改，但不是与原生 Agent 的原子锁，请避免同时编辑同一任务。完整支持范围见[内部 API](research-workspace-api.md#beads-任务面板)。

交互参考 [Bead Me Up, Scotty](https://github.com/brendan-appstart/bead-me-up-scotty) 的状态看板与详情、[Beadbox](https://github.com/beadbox/beadbox) 的项目内任务与依赖浏览；本面板使用 EasyRead 原有原生 JS、主题和 HTTP 服务独立实现，未复制或捆绑参考项目的代码、资源或服务。

## LaTeX 编辑器

在项目中点**LaTeX 编辑器**（`/workspaces?project=<id>&view=latex`）。左侧文件树只显示 `latex-workspace/`。首次进入创建目录，空目录初始化最小 `main.tex`；已有源文件不会覆盖。LaTeX 与项目主页共享 Agent 会话和关联文献。

主文件使用相对 `latex-workspace/` 的 `.tex` 路径，可选择 **pdfLaTeX、XeLaTeX、LuaLaTeX**。主文件/引擎保存在 `latex-workspace/.easyread-latex.json`，重开后继续使用。LaTeX 模式始终自动保存，编译前会等待待保存内容完成；不再显示单独的保存工具栏。顶部设置按钮展开主文件、引擎和编辑器字号，点击外部关闭。默认源码字号为 12px，可调整为 10–22px，与阅读器和 PDF 缩放独立。

PDF 采用本地 PDF.js worker 按需渲染（最多 6 页画布、2 个渲染任务），支持分页、缩放、适合宽度、文字选择和下载，并使用 HTTP 字节范围请求。日志按钮在 PDF 与编译日志之间切换。编译生成 SyncTeX 后，可用源码定位按钮或 Ctrl/⌘+Shift+Enter 跳到 PDF，双击 PDF 返回源码；源码或 PDF 改变后需重新编译，旧 PDF 也需重新编译一次才能定位。

LaTeX 文件树支持上传 ZIP：自动去除单层包装目录、识别主 `.tex` 文件，同名文件需确认覆盖，其他文件保留。目录/文件冲突拒绝替换，ZIP 不执行脚本、不自动编译。解压总量限制为 128 MiB/10,000 个条目。

服务端异步执行 **3 遍 TeX**；第一遍生成的 AUX/BCF 请求参考文献时，在中间执行一次 **BibTeX 或 Biber**，最后两遍解析引用与交叉引用。BibTeX 支持项目内 `.bib` 与项目内/已安装的 `.bst` 样式；Biber 支持项目内 BibTeX 格式的 `.bib`。缺失工具或文献处理失败时，编译报错，不会把缺失参考文献的 PDF 当作成功。PDF、编译日志和源快照保存在 `latex-workspace/.easyread-build/<运行ID>/`。每次使用独立输出目录，失败或取消不会将旧 PDF 误报为本次成功结果。服务中断后，重开时把运行状态标为中断，不自动续编。只有显式点击编译才启动 TeX。

限制：每个服务同时最多 2 次编译；默认**总共 120 秒**（`EASYREAD_TEX_TIMEOUT` 可设 5–600 秒）；捕获日志 2 MiB；源文件总量 128 MiB/10,000 个；PDF/输出预算 64 MiB。Linux 存在 `prlimit` 时还限制每进程文件大小、CPU 与 2 GiB 地址空间。拒绝符号链接、特殊文件、硬链接和越界路径。TeX 与参考文献子进程共享上述时间/日志/输出预算，取消可停止其中任一步。仅保留当前与前两次带 EasyRead 标记的构建目录，不删除未标记/用户创建的目录或旧版快照；所有编译结束后可手动清理这些旧目录。首次进入前的状态/忙碌检查不创建 `latex-workspace`，首次进入仍初始化空项目而不覆盖已有源码。

编译器使用隔离后的环境变量、TeX 严格读写路径设置、禁用 shell escape，LuaTeX 额外使用 `--nosocket`。`luaotfload/fontspec` 与 `--safer` 不兼容，因此不使用该选项：**LuaLaTeX 可执行文档内的 Lua 代码，具有服务账号的文件访问能力**，只能编译已检查并信任的文档；禁用 shell escape 不等于禁止 Lua 文件访问。**不执行**项目 `.latexmkrc`。依赖 shell 的包（例如 `minted`、外部化工具）在该编译器中不可用。执行前验证文献控制文件：Biber 使用 `--noconf`，拒绝远程资源、glob 通配符、外部 XML 实体、其他输入驱动以及改变文件系统路径的 BCF 选项。请将文献数据保存在当前项目的 LaTeX 目录内。固定的 TeX → 文献处理 → TeX → TeX 流程不会执行由项目控制的完整 `latexmk` 构建图。源码/桌面环境请先安装缺失的 TeX 包，不会在编译中请求无人值守安装。这些措施不能把同账号的完全权限 Agent 或有漏洞的 TeX 引擎变成系统沙箱，请勿编译公开上传的不可信材料。

## 安装与诊断

| 发行方式 | 已包含 | 还需准备 |
| --- | --- | --- |
| 完整 Docker | Git、官方 bd/Dolt、三种 TeX 引擎/latexmk/Biber/常见 LaTeX 与中文包、MCP Python 运行时、Codex/Claude、ttyd | CLI 登录、Git 身份/远程设置、访问保护 |
| Lite Docker | EasyRead/Python 和 MCP | 不包含原生 Agent、Git/bd/Dolt 或 TeX；完整研究环境请切换 full。文件编辑/文献访问仍可使用，缺失能力有诊断 |
| 桌面安装包 | 打包后的 Python 服务、MCP 与研究空间适配器 | 在宿主机安装 Git、bd、TeX 与所选 Agent CLI；PATH 修改后重启应用 |
| 源码 | `pip install -e .` 安装包含 MCP 的 Python 依赖 | 把相同的原生工具加入 PATH；重建编辑器/桌面资源需 Node 22 |

在源码检出目录中运行（打包后端旁也会附带该帮助脚本）：

```bash
# 只检查：不启动子进程，不下载，不登录，不初始化仓库。
python scripts/install_workspace_deps.py --check
# 显式安装：只下载官方 bd/Dolt 二进制并核对 SHA-256。
python scripts/install_workspace_deps.py --install
# 把 ~/.local/bin 加入 PATH，再重启 EasyRead。
```

默认版本的安装脚本支持 Linux/macOS amd64/arm64 和 Windows amd64。Windows arm64 请使用上游支持的 Dolt 包/模拟环境。`--prefix` 可指定安装前缀。脚本不会自动安装系统包、修改 shell 配置、初始化开发仓库或改变登录。下载遵循 `HTTPS_PROXY`，重试次数有界；会打印系统包安装命令供用户检查，TeX 可能占用数 GB。

**Debian/Ubuntu：**

```bash
sudo apt-get update
sudo apt-get install -y git texlive-latex-base texlive-latex-recommended \
  texlive-latex-extra texlive-fonts-recommended texlive-xetex texlive-luatex \
  texlive-lang-chinese texlive-bibtex-extra latexmk biber
```

**macOS：** `brew install git beads dolt`，再运行 `brew install --cask mactex-no-gui`；确保 EasyRead 能看到 `/Library/TeX/texbin`。**Windows：** `winget install Git.Git` 与 `winget install MiKTeX.MiKTeX`；在 MiKTeX Console 中提前安装/更新所需包。bd/Dolt 使用帮助脚本或[上游安装方式](https://github.com/gastownhall/beads/blob/v1.3.4/docs/getting-started/installation.md)。Agent 使用 [Codex](https://developers.openai.com/codex/cli/) 或 [Claude Code](https://code.claude.com/docs/en/setup) 的安装/登录说明，安装后重启桌面应用/服务。

TeX 不在标准 PATH 时，可为服务设置 `EASYREAD_TEX_PDFLATEX`、`EASYREAD_TEX_XELATEX`、`EASYREAD_TEX_LUALATEX`，值为可执行文件路径而非 shell 命令。`EASYREAD_TEX_BIBTEX` 与 `EASYREAD_TEX_BIBER` 同样可覆盖文献工具的可执行路径；网页只接收三个固定 TeX 引擎名。路径存在的绿色探测不等于论文需要的所有宏包都已安装。

## 部署、升级与验证

参见[完整 Docker 部署](docker.md)。`.env.example` 与 Compose 提供 `BEADS_VERSION=1.3.4`、`DOLT_VERSION=2.7.5`、`DOLT_SHA256`（换 Dolt 版本时必填）和 `EASYREAD_TEXLIVE_PACKAGES`（APT 包列表，镜像源保留版本时可用 `包名=版本`）。Codex/Claude 原有版本开关独立保留。完整镜像通过官方 npm 包安装两者的原生平台二进制（含 npm 完整性校验）；`CLAUDE_CODE_VERSION` 接受 stable/latest 或固定版本。`easyread-cli update` 同时兼容 npm 安装与既有原生安装，不改变登录目录。根 Dockerfile 还可用 `PYTHON_IMAGE`/`NODE_IMAGE` 固定基础镜像。安装脚本核对 bd 发行 checksum 清单与固定/手工覆盖的 Dolt 校验值，并保存第三方许可证。测试新依赖适配不需要改 EasyRead 版本号。

原生依赖变化必须重建**根 Dockerfile 的 full 目标**，不能只用 `docker/Dockerfile.app` 的代码更新路径；该路径检查新运行时，缺失时明确失败，不会把旧的 CLI-only 镜像伪装成完整研究镜像。数据与 CLI HOME 卷持续保留，依赖重建不覆盖用户登录文件。不要把 `docker compose down -v` 当作升级步骤。

升级 Beads 前阅读[上游升级说明](https://github.com/gastownhall/beads/blob/v1.3.4/docs/getting-started/installation.md#updating-bd)，完整备份后在副本验证。数据库迁移可能无法降级，EasyRead 不会在导入项目时自动运行上游迁移/hooks 安装命令。TeX 升级应使用实际论文和所选引擎重新编译验证。前端编辑器依赖独立构建，不需要部署 Aldine/TeXlyre 服务。

```bash
python -m unittest discover -s tests -p test_workspace_latex.py -v
# 在新构建的 full 镜像内，只使用临时项目与 HOME：
python /usr/local/bin/easyread-check-workspace-runtime
python /usr/local/bin/easyread-check-workspace-runtime --adapter
```

完整 Docker 构建会执行上述真实原生/适配器烟测，包括 `bd init/create/list`、三种 TeX 引擎、PDF 与错误行号，并通过异步适配器从 BibTeX/Biber 生成的 PDF 中提取真实参考文献文本。标有 **MOCK** 的单测只验证进程控制与安全逻辑，不能证明真实 TeX 已安装可用。桌面发行还需各原生平台验证安装、PATH 与编译，Linux 镜像通过不代表 Windows/macOS 安装验收通过。

## 上游参考与许可

设计参考：[Aldine](https://github.com/trahloff/Aldine) 的项目输出持久化与错误行号、[TeXlyre](https://github.com/TeXlyre/texlyre) 的源码/PDF 分栏与编辑反馈。两者均为 **AGPL-3.0**，这里未复制或打包其源码/素材；EasyRead 服务端编译器是独立适配器，不是它们的编译服务或 WASM 引擎。[Beads](https://github.com/gastownhall/beads) 为 MIT，[Dolt](https://github.com/dolthub/dolt) 为 Apache-2.0，TeX Live 各包遵循各自许可证。另见 [TeX Live 文档](https://www.tug.org/texlive/doc.html) 和 [LuaTeX 选项](https://manpages.debian.org/bookworm/texlive-binaries/luatex.1.en.html)。

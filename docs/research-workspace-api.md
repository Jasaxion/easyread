# 研究空间内部 API（1.6）

[用户指南](research-workspaces.md) · [数据格式](data-format.md)

这些是随研究空间界面迭代的内部接口，不属于稳定开放 API v1。前缀为 `/api/workspaces`，下文 `/{id}` 是不可更改的项目 ID。所有请求通过原有 Host/访问 Cookie 保护，POST 还需要 `X-Token`。JSON 返回错误为 `{error,code?}`，文件冲突为 HTTP 409，越界/链接访问为 403，缺失资源为 404。不得自动重试 AI send、Git 写入或文件写入。服务重启后重新获取 token。

## 项目和文件

| 方法 / 后缀 | 请求 / 返回 |
| --- | --- |
| GET `/`（即前缀本身） | 可选 `?q=` 名称/简介检索；`{projects,capabilities,library_id,token}`。能力探测不调用模型 |
| POST `/create` | `{name,description,engine:codex\|claude}` → `{project}`；初始化说明文件、Git 与可用的 Beads，错误在 `initialization` 显示 |
| GET `/{id}` | `{project,workspace_root}`；后者是已授权的项目绝对路径，用于准确解析 AI 文件链接 |
| POST `/{id}/update` | `{name?,description?}` → `{project}`，不改变目录 |
| POST `/{id}/delete` | `{}` → `{ok}`；运行中任务需先停止 |
| GET `/{id}/tree?path=` | 空 path 为完整项目；或 `latex-workspace` 子树；`{entries:[{path,name,kind,size,version}],version}`，路径相对项目根 |
| GET `/{id}/file?path=` | `{path,version,binary,mime,size,content?}`；文本编辑上限 8 MiB |
| GET `/{id}/raw?path=` | 原始字节；PDF/安全栅格图像可预览，HTML/SVG 等主动内容作为附件，不作为同源页面运行 |
| POST `/{id}/files/create` | `{path,kind:file\|directory,content?}`，目标必须不存在 |
| POST `/{id}/files/write` | `{path,content,version}`，必须携带原内容版本。409 时保留浏览器草稿、读取最新版本或另存副本 |
| POST `/{id}/files/move` | `{path,destination}`，用于移动/重命名，目标必须不存在 |
| POST `/{id}/files/delete` | `{path}`；删除目录递归生效，前端需明确确认 |
| POST `/{id}/files/upload?path=` | 请求体为原始字节，最大 200 MiB；不静默覆盖已有文件 |

树 `version` 用于外部变更提示，不是文件写入令牌。写入使用 `/file` 的内容版本。隐藏文件和配置可管理；符号链接在树中显示但不能跟随。工作目录不是对完全权限 Agent 的系统沙箱。

## 文献、会话和 Git

| 方法 / 后缀 | 请求 / 返回 |
| --- | --- |
| GET `/{id}/papers?q=` | `{linked,available}`，复用现有文献摘要；不存在的引用保留并标记 missing |
| POST `/{id}/papers/link`、`/papers/unlink` | `{document_id}`，只更改引用，不复制或删除文献 |
| GET `/{id}/models?engine=codex\|claude` | `{engine,default,models:[{id,name,description?,source,reasoning_levels?,default_reasoning?}],reasoning_levels,configured_reasoning,source,installed,warning?}`；`refresh=1` 请求刷新，受缓存/并发/超时限制。仅目录元数据，不启动模型对话；不返回配置路径、服务地址或凭据 |
| GET `/{id}/sessions` | `{sessions,engines,capabilities,runtime}`，列表仅返回摘要，不含 messages/events；能力在 `capabilities.engines`，不会启动 Agent |
| POST `/{id}/sessions/create` | `{engine,title?,model?,reasoning_effort?,native_id?}` → `{session}`，恢复 ID 必须属于本项目；强度空字符串表示跟随 CLI 默认 |
| POST `/{id}/sessions/{sid}/settings` | `{model?,reasoning_effort?}` → 近期快照；只更新无活动工作的会话，保留原生 ID/历史，遗漏字段保持不变。活动轮次、后台任务、队列、待回复、终端占用或仍保留的活动目标返回 409 |
| GET `/{id}/sessions/{sid}` | `{session,cursor,history_before,has_more}`；近期快照最多 40 条消息/200 条事件，包含 pending_requests 和原生目标/运行状态 |
| GET `/{id}/sessions/{sid}/updates?after=&limit=100` | 摘要、pending_requests、消息 upsert 与追加事件；返回 next_cursor/has_more，reset=true 时重新读取快照 |
| GET `/{id}/sessions/{sid}/history?before=&limit=40` | 读取较早消息与事件，返回 history_before/has_more；消息按 id、事件按 seq 去重 |
| POST `/{id}/sessions/{sid}/send` | `{message,files?:[相对路径],mode?:chat\|goal}` → 近期快照；默认普通执行，Skills/插件不依赖 Goal。最多 32 个现有项目文件，作为路径上下文，不复制文件内容；同一原生会话不能重复启动活动轮次 |
| POST `/{id}/sessions/{sid}/goal` | `{action:set\|pause\|resume\|clear,objective?}`；原生可选目标，不模拟连续发送“继续” |
| POST `/{id}/sessions/{sid}/steer` | `{message,files?,expected_turn_id}`；追加到观察到的当前轮，过期返回 409，不另开一轮 |
| POST `/{id}/sessions/{sid}/respond` | `{request_id,generation,answers?,decision?,values?}`；仅回复仍属于当前运行实例的原生待处理请求 |
| POST `/{id}/sessions/{sid}/cancel` | `{}`，停止本会话任务，保留已保存记录 |
| POST `/{id}/sessions/{sid}/rename` | `{title}` |
| POST `/{id}/sessions/{sid}/delete` | `{}`，删除项目会话记录；运行中先停止 |
| POST `/{id}/sessions/{sid}/terminal` | `{}` → `{url,...}`，受访问会话保护的原生 CLI 终端，需要 ttyd；当前授权已有的终端直接复用 URL，不重启。UI 同源 iframe 嵌入并在切回 GUI 时保留连接，明确停止仍用 `/cancel` |
| GET `/{id}/git` | `{branch,status,remotes}` |
| POST `/{id}/git` | `{action:status\|commit\|remote\|pull\|push,message?,url?}`；提交需 message，设置 origin 需 url，pull 只 ff-only，无强推 |

`reasoning_effort` 为短字符串标识：Codex 优先校验模型已公布档位，未知模型使用引擎候选且保留模型端最终校验；Claude 支持 low/medium/high/xhigh/max。字段保存在会话摘要中，旧会话缺失时视为继承。Codex 使用线程配置和 TUI `-c model_reasoning_effort=...`，Claude 使用 `--effort`，均不修改共享配置。恢复已有 Codex 对话时空设置必须从原生 `config/read`/`model/list` 解析实际默认再显式应用；不能以省略参数保留旧覆盖，也不能传 null。无法解析默认时返回 defaults_unavailable，用户可选择明确型号/强度。

增量轮询不启动运行实例。`state` 区分 running、waiting_user、waiting_external、paused、completed、interrupted 等；`runtime_state` 独立表示实例是否已回收。运行实例可能按能力给出更准确的 `session.capabilities`，优先于安装级能力探测。状态变化和问题的产生/解决也推进游标，不要求有新文本。

请求 ID 必须保留原生字符串/数字类型，并携带 generation；不重用重启前的待回答句柄。问题使用 `{answers:{question_id:{answers:[字符串]}}}`；审批仅允许显式 `accept`/`decline`，不接受客户端指定更广的权限。MCP 表单使用 `values`，只接受已支持且校验通过的基础 schema，其他流程可拒绝。未经验证的请求不得自动接受。

429 表示容量已满，不代表请求进入持久队列；不要自动重提发送。RPC 结果不确定或服务重启时显示 reconciliation_required，需核对已有实验/结果并明确停止或恢复。普通执行与 Goal 均独立于页面生命周期，但不保证独立于 EasyRead 后端或原生进程退出。

原生会话独立于原有阅读器 chat.json；研究空间采用用户明确要求的完全权限，不修改阅读/翻译策略。项目 MCP 通过私有 stdio 子进程提供文献搜索、正文、笔记、批注和当前项目关联操作，不需要把公开 API Bearer 密钥写进项目。CLI 登录/原生配置由服务账号管理。

Git 管理动作禁用 hooks、自定义过滤器和不安全配置；不执行导入配置中的自定义命令。网络操作只在显式 pull/push 后发生。远程支持 HTTPS、SSH 和显式绝对本地路径（离线 bare 仓库），不支持 `ext::` 等 helper 协议。

## 项目归档

- GET `/{id}/export` 下载 `.easyread.zip`；POST `/import` 接受原始 ZIP，返回 `{project}`。
- 归档包含项目文件（含隐藏文件、空目录、执行权限、Git 和 Beads 数据）、元数据、关联 ID、聊天及项目拥有的原生会话状态；宿主登录凭据与无关会话不导出，关联文献本体不复制。
- 导入前完整验证路径、类型、重复项、条目数与解压大小；不支持链接/特殊文件/加密 ZIP。上传上限 200 MiB，解压上限 512 MiB、50,000 条目。更大工程请使用文件系统备份与 Git。
- 导入分配新随机 ID，任何导入文件在此过程中均不会执行。分享前检查用户或 Agent 自行写入文件的密钥。

消息与工具事件可携带 `turn_id`；消息 `id` 和事件 `seq` 用于增量去重。首次访问会把旧 state.json 复制进 SQLite journal，保留原文件不覆盖；后续历史与快照以 journal 为准。旧 state.json 不是新内容的备份，不应单独用于回滚。完整导出仍包含全部会话历史，不以界面的近期窗口为限。

## LaTeX

- GET `/{id}/latex`：首次初始化 `latex-workspace`，返回 `{main,engine,engines,compilation}`，不启动编译。
- POST `/{id}/latex/settings`：`{main,engine}`。main 相对 `latex-workspace`；固定引擎 `pdflatex/xelatex/lualatex`。
- POST `/{id}/latex/compile`：`{main?,engine?}`，立即返回后台状态；通过 GET latex 轮询。
- POST `/{id}/latex/cancel`：停止当前编译并保留源码。
- POST `/{id}/latex/forward-sync`：`{pdf,path,line,column}` → `{found,matches:[{page,x,y,width,height,page_width,page_height}],pdf}`。path 相对项目根，以 `latex-workspace/` 开头。
- POST `/{id}/latex/inverse-sync`：`{pdf,page,x,y}` → `{found,path,line,column,pdf}`。坐标均为 PDF 点、左上角原点，页码从 1 开始；必须绑定当前编译 PDF。陈旧 PDF/源码返回 409 `stale_pdf`/`stale_source`。
- POST `/{id}/latex/import-zip?replace=0|1`：原始 ZIP 字节 → `{imported,main,engine,compilation,engines}`。默认同名文件返回 409 `file_conflict`，确认后 `replace=1`；文件/目录类型冲突为 `type_conflict`。运行中项目拒绝导入，先完整验证再发布并支持失败回滚。
- `raw?path=` 支持单个字节 Range、206/416、HEAD 与 64 KiB 分块读取，供 PDF.js 按需加载；认证与主动内容附件限制不变。
- compilation 包含 `state:idle|running|done|error`、`log`、`errors:[{file,line,message}]`、成功时 `pdf` 与可选 `synctex`。pdf 为相对项目根的路径，通过 raw 接口读取。
- 编译是保存文件的快照；前端先处理脏缓冲/冲突。失败、中断或取消不把旧 PDF 标记成新结果。资源限额、包安装及信任边界见[用户指南](research-workspaces.md)。

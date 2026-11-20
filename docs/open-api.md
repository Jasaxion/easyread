# 开放 API 与 MCP / Open API and MCP

## 启用

现有网页及旧 `/api/*` 不变。新 API 和 MCP 默认关闭，重启后读取启用配置。源码安装：

```sh
python -m pip install -e '.[mcp]'
EASYREAD_OPEN_API=1 EASYREAD_MCP=1 python -m easyread serve --port 8765
```

只用 REST 不需要 `mcp` extra。MCP 通过官方 Python MCP SDK，复用 EasyRead HTTP 监听端口，不启动第二个网络服务。安装包或旧 Docker 镜像未包含该可选依赖时，须先安装依赖并部署本分支代码；仅设置开关不会升级镜像。

也可在 EasyRead 数据目录 `config.json` **合并**以下配置（不要覆盖已有模型配置）：

```json
{"open_api":{"enabled":true,"mcp_enabled":true,"public_url":"http://localhost:8765"}}
```

对应环境变量 `EASYREAD_OPEN_API=0|1`、`EASYREAD_MCP=0|1`、`EASYREAD_PUBLIC_URL` 优先。两个开关可独立设置。远程访问沿用 `--host` / `--port` 和 `EASYREAD_ALLOWED_HOSTS`；反向代理需保留 Host。`public_url` 指客户端可访问的完整服务基址（可含代理路径前缀），用于生成文章/任务链接；未配置时用服务器本机地址。它不会改变监听地址。

## 共用访问授权码

沿用“设置 → 访问保护”，不新增密码、账号或 OAuth：

- **开启**：REST 和 MCP 每个请求携带 `Authorization: Bearer <现有访问授权码>`。
- **关闭**：REST 和 MCP 均无需鉴权，包括远程客户端。
- 更换授权码立即影响之后的请求，旧码失效；已提交的后台导入继续运行。开关无需重启。
- 首次配置仍可用 `EASYREAD_ACCESS_CODE`，之后以已有 `.access.json` 和界面设置为准。
- 新接口不要求浏览器 Cookie 或 `X-Token`；Bearer 也不能访问终端、系统设置等旧内部接口。旧网页的 Cookie/CSRF 逻辑保持不变。
- 保留 Host、Origin 检查及失败尝试限流，不允许跨站网页调用。远程/公网建议开启保护并使用 HTTPS；关闭保护相当于允许可连接的客户端读取文献库和提交导入。不要把授权码写进 URL 或提交到 Git。

## REST API

基址 `http://localhost:8765/api/v1`；机器可读规范 `GET /api/v1/openapi.json`，同样遵循授权开关。

| 方法 | 路径（省略 `/api/v1`） | 用途 |
|---|---|---|
| GET | `/info` | 版本、能力、开关、上传和分页上限 |
| POST | `/imports/file` | 原始 PDF 字节，`Content-Type: application/pdf`；query 为 `filename`、`translate=true|false`、`target` |
| POST | `/imports/html` | JSON `{html, title?, source_id?, translate?, target?}`；私有文档正文快照，不需要公开 URL |
| POST | `/imports/url` | JSON `{url, translate?, target?, kind?}`；`kind=auto|pdf|web` |
| GET | `/tasks/{task_id}` | 持久化导入状态及文档当前处理进度 |
| GET | `/documents` | `q` 搜索标题/作者/摘要等；`category` 含子分类；`status` 过滤阅读状态 |
| GET | `/documents/{id}` | 文档详情、来源、分类、进度、链接 |
| GET | `/documents/{id}/notes` | `paper_note` 整篇笔记与分页 `items` 段落笔记/问题 |
| GET | `/documents/{id}/highlights` | 划线、颜色、摘录、锚点和链接 |
| GET | `/documents/{id}/whiteboard` | 可选白板的场景 JSON、版本，以及 PNG 预览的就绪状态和 URL |
| GET | `/documents/{id}/whiteboard/image` | 当前版本 PNG；可带 `revision` 固定读取的版本 |
| GET | `/documents/{id}/content` | `language=original|translation|both`，`view=effective|raw` |
| GET | `/categories` | 按树排序的分类列表：`path`、`parent`、`count`，包含空分类 |
| GET | `/documents/{id}/links` | 浏览器/桌面链接；可加 `block` 定位原句 |

列表、笔记、划线、正文、分类共用 `limit`（默认 50，上限 200）和 `offset`（默认 0）。返回 `{items,total,limit,offset,next_offset}`；`next_offset=null` 表示没有下一页。文档列表按添加时间倒序、ID 排序；分页不是跨请求快照，并发改动时建议重新查询。

正文保持现有段落/图表/公式结构。历史字段 `en` / `zh` 表示原文/目标译文，不一定是英文/中文；列表项和题注也保留双语字段。`effective` 应用用户修订，`raw` 返回原译稿。未整理的 PDF 可能没有结构化正文，不能把空列表当作翻译成功。所有读取不标记已读、不触发翻译或补图。

### 白板与 AI 图像读取

`GET /documents/{id}/whiteboard` 返回 `{document_id, board, preview}`。没有白板时 `board=null`，不会创建白板。`board.scene` 是 Excalidraw 场景，含元素、图片和素材库；`preview.status` 为 `absent`、`pending` 或 `ready`，就绪时 `preview.url` 带对应的 `revision`。`GET /documents/{id}/whiteboard/image?revision=<revision>` 返回 `image/png`，没有白板为 404，版本变化或尚无当前 PNG 为 409。

预览由阅读器在打开白板及编辑保存后空闲时生成，最长边最多 1024 像素，适合 AI 获取概览；精确文本和结构请同时读取场景 JSON。API 读取不启动浏览器或渲染任务。删除白板后图像接口不可用；删除文档时白板/预览与笔记一起进入回收站、恢复或永久清理。Bearer 接口只读，不提供创建、修改或删除白板的权限。

```sh
curl -H "Authorization: Bearer $CODE" "$BASE/api/v1/documents/$ID/whiteboard"
curl --fail -H "Authorization: Bearer $CODE" \
  "$BASE/api/v1/documents/$ID/whiteboard/image?revision=$REVISION" -o whiteboard.png
```

**English:** Read the optional scene and PNG readiness through `/documents/{id}/whiteboard`, then fetch `/whiteboard/image` with the same Bearer authorization. Pin `revision` from the JSON response; missing boards return 404, pending/stale previews return 409 rather than an outdated image. Opening/saving a board in the reader produces a PNG overview (maximum edge 1024 px). Reads do not render or create anything. These endpoints are read-only; MCP tools are unchanged.

### 导入与任务

`POST` 返回 **202** `{task_id,state:"queued",status_url}`；默认 `translate=false`，开启翻译可能调用收费模型。上传上限 50 MiB；单队列最多等待 8 个任务。只接受文件字节，不允许服务端文件路径。重复导入沿用既有指纹/URL 去重，不覆盖笔记，也不会强制重译已有文档。

任务 `state`：`queued|running|done|error|interrupted`。**`done` 只表示已导入，绝不代表翻译完成。** `document_id` 和链接供后续读取；`processing` 是该文档当前准备/翻译任务，包含 `state`、`done`、`total`、`error`。它可能为 `null`；`confirm` 需到现有界面确认页数，`partial` 代表部分失败。以后在界面发起的新任务会替换这个当前状态。

记录保存在文献库 `.open-api-tasks/`，重启保留查询。下载/落库中断的任务标记 `interrupted`，不静默重放；已入现有 Jobs 队列的准备/翻译沿用既有续跑行为。

URL 必须是公网 HTTP(S)（仅端口 80/443），支持现有 PDF、静态网页、arXiv 等 **URL** 来源，不接受裸标题/本地路径。首跳、重定向、引用 PDF、网页资源及公开导入的后台元数据获取均执行公网 IP 检查，实际连接固定到已验证 IP，阻止 DNS 重绑定。禁止 URL 内凭据。公开导入不继承代理，不执行网页 JavaScript；需要 JS 或登录的页面请下载 PDF 后上传。下载有 180 秒预算、单次连接超时和累计大小/次数上限；资源归档失败可沿用现有网页降级逻辑。

错误统一为 `{"error":{"code":"…","message":"…"}}`。常见 HTTP 状态：400 参数或来源不合法，401 缺失/错误授权，403 Host/Origin 不符，404 不存在或未启用，405 方法不支持，413 超限，415 格式不符，429 限流/队列满，503 MCP 依赖缺失。后台失败见任务 `error`；不会返回服务器路径、代理凭据或原始上游错误内容。

### 私有 HTML / 思源文档导入

`POST /api/v1/imports/html` 接收 UTF-8 HTML（最多 8 MiB；JSON 请求体最多 12 MiB），`title` 最多 500 字符，`source_id` 是最多 512 字符的可选来源标识。`GET /info` 用 `capabilities` 中的 `import_html` 和 `max_html_bytes` 标识支持。短篇笔记可直接导入，无需公网发布、临时 HTTP 文件服务器或服务端本地路径。

沿用 Bearer、202 与任务查询；`translate` 默认为 false，已解析的 HTML 此时不再排预处理/模型任务。导入只解析 HTML，不联网、不执行脚本，也不运行浏览器生成封面。清除脚本、表单、iframe、SVG、CSS 和远程媒体；仅保留内嵌 PNG/JPEG/GIF/WebP 图片与安全超链接。需要本地图片时，由调用方读取后内嵌 data URI。不是完整网页归档或思源数据库/挂件迁移。

`html + title + source_id` 一起参与指纹：完全相同的输入去重，不覆盖已有笔记；正文或标题改变时建立新快照文章。不会把 `source_id` 当成 URL 抓取。

[思源插件](https://github.com/Jasaxion/siyuan-easyread)通过思源 `kernel.js` 导出 Markdown、调用 Lute 转 HTML，再提交此接口；PDF 使用原始字节接口。绑定文章后可单向拉取笔记、划线和双语正文到思源快照子文档。

### curl 示例

```sh
BASE=http://localhost:8765
# CODE 在本地安全输入；关闭访问保护时删去 Authorization 头。
curl -H "Authorization: Bearer $CODE" "$BASE/api/v1/documents?limit=10"
curl -H "Authorization: Bearer $CODE" -H 'Content-Type: application/pdf' \
  --data-binary @paper.pdf "$BASE/api/v1/imports/file?filename=paper.pdf&translate=false"
curl -H "Authorization: Bearer $CODE" -H 'Content-Type: application/json' \
  -d '{"url":"https://arxiv.org/abs/2305.18290","translate":false}' "$BASE/api/v1/imports/url"
curl -H "Authorization: Bearer $CODE" "$BASE/api/v1/tasks/<task_id>"
```

Private-document clients can use `POST /api/v1/imports/html` with `{html, title?, source_id?, translate?, target?}`. HTML is limited to 8 MiB UTF-8 (12 MiB JSON envelope), title to 500 characters, and the opaque source ID to 512. Detect `import_html` in `/info.capabilities`. HTML is parsed offline: no fetching, JavaScript, CSS, remote media or cover browser; only embedded PNG/JPEG/GIF/WebP images and safe hyperlinks survive. Short notes are accepted. The input triple determines deduplication; changed content creates a new article without overwriting existing notes. Default `translate=false` schedules no further work for this already-structured source. The [SiYuan plugin](https://github.com/Jasaxion/siyuan-easyread) uses this route from its kernel plugin and pulls selected content into append-only child snapshots.

## MCP 连接

使用 **Streamable HTTP** 地址 `http://localhost:8765/mcp`，不是旧 SSE `/sse`。服务采用无状态 JSON 响应，`GET/DELETE /mcp` 返回 405 是正常的；无需保存会话 ID。

支持自定义请求头的客户端配置示意（具体字段以客户端为准）：

```json
{
  "mcpServers": {
    "easyread": {
      "type": "http",
      "url": "http://localhost:8765/mcp",
      "headers": {"Authorization": "Bearer <现有访问授权码>"}
    }
  }
}
```

访问保护关闭时删除 `headers`。此版本不提供 OAuth 自动登录，只支持 URL＋自定义 Bearer 头（或无授权）；仅支持 OAuth 的客户端不能直接接入。

工具：`get_info`、`list_documents`、`get_document`、`get_notes`、`get_highlights`、`get_content`、`list_categories`、`get_document_links`、`import_url`、`get_task`。参数与 REST 对齐，文档 ID 参数名为 `document_id`。文件先经 REST 上传，再用 `get_task` 查询，不向模型传输大段 base64，也不开放任意服务器路径。

官方 SDK 示例（`EASYREAD_CODE` 由调用端安全配置，未设置表示不带授权）：

```python
import asyncio, os
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    code = os.environ.get("EASYREAD_CODE")
    headers = {"Authorization": f"Bearer {code}"} if code else {}
    async with httpx.AsyncClient(headers=headers) as http:
        async with streamable_http_client("http://localhost:8765/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print(await session.list_tools())
                print(await session.call_tool("list_documents", {"limit": 10}))

asyncio.run(main())
```

## English quick start

Install the branch with `python -m pip install -e '.[mcp]'`. Enable REST and/or MCP using `EASYREAD_OPEN_API=1` / `EASYREAD_MCP=1`, then restart EasyRead. Both default to off. The matching `config.json` keys are `open_api.enabled`, `open_api.mcp_enabled`, and `open_api.public_url`; environment variables override them. REST-only users need no MCP extra. Older packaged/Docker builds must be updated with this code and the optional dependency first.

REST lives at `/api/v1`; OpenAPI 3.1 is served at `/api/v1/openapi.json`. MCP uses the official SDK with **stateless Streamable HTTP** at `/mcp` on the same listening port. Use the connection and SDK examples above. GET/DELETE on `/mcp` return 405 by design; no separate SSE endpoint or OAuth login is provided.

**The existing Settings → Access protection switch is authoritative:** enabled means every REST/MCP request needs `Authorization: Bearer <existing access code>`; disabled means no authentication, even remotely. Changes and code rotation affect subsequent requests immediately. Cookies/X-Token remain exclusive to existing browser routes, and Bearer does not unlock system settings or the terminal. Set an external `EASYREAD_PUBLIC_URL` for usable remote reading/task links; preserve Host through your proxy, configure the existing allowed-host list, and use HTTPS for network access. Never put codes in URLs or Git.

The endpoint table and schemas above define all parameters. Lists, notes, highlights, blocks and categories share `limit` (1–200, default 50), `offset`, and `{items,total,limit,offset,next_offset}`. Categories are ordered full paths with parent/count, including empty categories. Document search covers metadata; category filters include descendants. Reads never mark a document opened or trigger model work. Content defaults to both languages and effective user edits; `view=raw` reads translator output. Historical `en`/`zh` keys mean source/target language, not necessarily English/Chinese.

Uploads are raw PDF bytes, maximum 50 MiB, never server paths. URL imports accept public HTTP(S) URLs on ports 80/443, including article/PDF/arXiv URLs. Initial requests, redirects, PDF references, assets and subsequent preparation metadata are guarded against private/reserved addresses and DNS rebinding. Public imports bypass inherited proxies and do not execute webpage JavaScript; upload a PDF for login/JS-only sources. Downloads are bounded by time, size and request count. Translation defaults to **false**; opting in may use paid models. Existing deduplication preserves notes and does not force retranslation.

Import submission returns 202 with `task_id`. **Task `state=done` means imported, not translated.** Inspect `processing` for the document's current preparation/translation progress; `confirm` requires the existing UI, `partial` indicates partial failure, and no current job is `null`. Later document jobs may replace that current processing state. Import records survive restarts; uncertain imports become `interrupted`, not silently repeated. Existing processing jobs retain their normal recovery behavior.

REST errors use `{error:{code,message}}`; MCP tool failures return `isError` with the same error object. Auth and transport errors retain their HTTP/MCP protocol semantics. HTTP 401 means missing/invalid code, 403 Host/Origin rejection, 404 missing/disabled endpoint, 413 oversized upload, 429 rate/queue limit and 503 missing MCP dependency. This version deliberately excludes note/category mutation, deletion, chat APIs and multi-user permissions.

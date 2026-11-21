# 数据格式

## whiteboard.json（按需创建）

每篇文献目录下可选的 `whiteboard.json` 独立保存白板，不加入 `reader.json` 或每次阅读的 state 响应。只有用户明确创建才产生文件；已有文献无需迁移。

```json
{"schema": 1, "revision": "opaque-revision", "updated": "ISO timestamp", "scene": {"type": "excalidraw", "version": 2, "source": "EasyRead", "elements": [], "appState": {}, "files": {}, "libraryItems": []}}
```

预览是同目录的 `whiteboard-preview-<revision>.png`，只为当前已保存场景生成；成功更新预览时清理旧版本图片。白板读取返回预览就绪状态，当前版本没有对应 PNG 时不会回退到旧图。删除白板会移除 `whiteboard.json` 和预览；文献回收/恢复/永久删除仍沿用整个目录的操作，因此白板与笔记不会脱离当前文档。

成功保存另记录最后一个 `mutation` 和场景 `digest`，以幂等恢复丢失的确认。写入使用文献目录锁、fsync 与原子替换；损坏文件拒绝覆盖。引用卡片保留文本快照、`/read/<id>#b-<anchor>` 链接与 `customData.easyread` 来源元数据。图片以 data URL 嵌入，随文献目录备份/移动；不能只备份 reader.json。单个场景最多 32 MiB、20,000 个元素及 2,000 个素材库条目。

浏览器 IndexedDB `easyread-whiteboard-drafts` 仅保留未确认的编辑，按源站、`library_id:paper_id` 和独立编辑会话隔离，包含场景/图片、基础版本及待重试的不可变请求。授权读取对应库后才提供恢复入口，确认保存后清除。PWA 默认应用壳不包含 Excalidraw 大资源；独立 HTML 也不嵌入白板，请另外导出 `.excalidraw`。

## 浏览器离线副本

服务器仍以现有文献目录和 JSON 为正本，不迁移数据库。库根目录 `.pwa-id` 是随机身份标记，结合库路径生成不透明缓存标识；只读库不能创建标记时使用当前服务进程的临时身份，不阻塞正常读取。

启用 PWA 后，IndexedDB `easyread-offline` 保存正文/已确认笔记快照、文献摘要和缓存元数据；Cache Storage 保存版本化应用壳及按库隔离的相关阅读资源，不自动下载原 PDF。未提交修改使用 `pr-outbox-<library_id>-<paper_id>-<operation_id>` 独立 localStorage 记录，避免标签页间互相覆盖；服务端确认及本机快照落盘后才清除。清理浏览器数据仍可能丢失未同步修改，请保留服务端备份并留意存储告警。

## 后台链接导入记录

`.research-tasks/<id>.json` 中 `kind=import` 的记录保存已验证的原始输入/选项（`request`）、`request_id`、库身份、阶段、结果和重试关联。提交必须成功落盘才返回任务确认；重启后未结束任务标记为中断，不自动抓取或调用模型。任务 ID 按库作用域和请求 ID 确定，重复确认不会重新执行。`paper_id` 表示已经保存的文献，即使后续失败或取消也不会自动删除。

`retry_of`/`retry_task_id` 关联手动重试；`retry_pdf=true` 的任务只重新安排已有文献的 PDF 生成。BabelDOC 状态增加可选 `model_id`，保留用户所选模型条目供刷新后的失败重试使用，`model` 仍是已解析的显示名称。

## 模型条目的问答能力

应用 `config.json` 的 `qa_cli.claude/codex` 仍作为共享默认值；可选的 `qa_model_policies` 按 `chat.models[].id` 保存 `{engine, settings}` 独立覆盖。没有覆盖即继承默认，不在启动时改写旧配置。保存模型列表时清理已删除或 CLI 类型改变的条目策略；仅改名称/型号则保留同一条目的策略。该字段不能通过通用配置保存接口写入，需使用问答能力专用接口。

运行时 `_qa_model_id` 只用于明确的用户问答选择策略，不写入配置；翻译配置会移除此标记与活动问答权限并强制精简。模型卡片的名称、型号 ID 和问答能力是独立概念；空型号意味着跟随该 CLI 原生默认。

## paper.json

```json
{
  "schema": 2,
  "meta": {
    "title_zh": "给评测加上误差条：语言模型评测的统计学方法",
    "short_zh": "给评测加上误差条",
    "title_en": "Adding Error Bars to Evals: ...",
    "authors": "Evan Miller", "affiliation": "Anthropic",
    "date": "2024 年 11 月 4 日", "arxiv": "arXiv:2411.00640v1 [stat.AP]",
    "pdf": "source.pdf", "source_sha256": "…", "pages": [{"n": 1, "w": 612, "h": 792, "img": "pages/page-001.webp"}]
  },
  "translation": { "scope": "全文", "done_pages": [1, 2, 3], "note": "参考文献保留原文" },
  "glossary": [{ "en": "standard error", "zh": "标准误", "note": "可选" }],
  "references": [{ "id": "1", "text": "原文条目", "page": 12 }],
  "blocks": [ … ]
}
```

`translation.en_pages`：用“只读原文”整理过、还没翻译的页（这些页的块只有 `en`，没有 `zh`；页面直接排英文）。翻译它们时就地补 `zh`，块 id 不变，补齐的页从 `en_pages` 去掉。`done_pages` 包含这些页。

`meta.pages`、`page_count`、`source_sha256`、`pdf` 由导入时写，不要手改。文献库页里改的标题、作者等存在 `item.json` 的 `meta_override`，不改 paper.json。

`references` 的 `id` 通常是论文的引用键：编号式列表是编号（`"13"`），作者-年份式是用连字符连接的姓和年份（如 `Jimenez-2024`、`Chen-Jiang-2025`、`Chen-2026a`），字母标签式是标签（`JYW+24`）。缺少引用键时按条目内容生成 ID。同一键有不同条目时，每条保留独立 ID，原键写在 `cite_key` 中，正文不跳转到有歧义的目标。`page` 是翻译时这条所在那批的第一页，条目按它排序。参考文献表仅显示编号式、字母标签式的原标签。

### 块

每块必须有唯一 `id`（后续讨论、笔记都锚在它上面，定下后不要改）、`type`、`page`（这块在原 PDF 从哪页开始）。

| type | 字段 | 说明 |
|---|---|---|
| `heading` | `level`(1/2)、`num`、`zh`、`en`、`appendix` | 章节标题。`num` 如 "2.1"、"A"；附录的标题加 `"appendix": true` |
| `para` | `zh`、`en`、`role`、`num`、`cont` | 段落。`role: "abstract"` 用摘要样式；`role: "footnote"` 是脚注，`role: "listing"`/`"algorithm"` 是带编号的代码清单/算法，`num` 是它们的原编号；`cont: true` 表示接着公式的半句（如 “其中 …”） |
| `list` | `ordered`、`items: [{zh, en}]` | 列表 |
| `math` | `tex`、`tag` | 行间公式。`tag` 是原文编号（"1"），无编号不写。多行用 `aligned` / `gathered` |
| `table` | `num`、`head: [[…]]`、`rows: [[…]]`、`align`、`caption_zh`、`caption_en`、`caption_pos` | 表格。单元格支持行内标记，`\n` 换行（第二行括号内容自动变灰，适合“均值\n(标准误)”）。第一列行标签每层缩进用两个前导空格。`align` 如 "lrrr" |
| `figure` | `num`、`src`、`box`、`image_en`、`image_zh`、`caption_zh`、`caption_en` | 图。`src` 是论文目录下的图片（如 `figures/crop-4-….webp`），留空时页面显示“图见原文第 N 页”。`box`、`image_*` 见下文“图：裁图和图内文字” |
| `references` | `zh`、`en` | 放参考文献列表的位置（内容取 `references`） |
| `note` | `zh` | 正文流里的“阅读批注（非原文）”。尽量不用，解释放 discussion.json |

可选 `box: [x0, y0, x1, y1]`（按页宽高归一化）手工指定原页高亮区域，覆盖自动定位。

图块可额外带 `translated_image: {src, source, target, image_en, image_zh}`，由图片编辑步骤写入。`src` 是生成的 WebP 相对路径；`source` 是原图路径；其余字段记录生成时的目标语言及图内文字。原图仍保存在块的 `src`，文字译文仍在块的 `image_zh`。生成时优先采用 `reader.json` 中的文字修订作为参考，不改写修订或原稿。只有记录与当前原图、语言及有效文字一致时，阅读页才显示译图；否则显示原图和文字译文。可用译图与原图沿用正文的单语／双语选择和双语顺序，不再展示图内文字译文；图注仍按正文设置显示。图片加载失败时恢复原图和文字兜底。生成失败不改变页面的翻译完成状态，失败原因写入 `job.log`。取消后不采用迟到结果，不自动重发图片请求。重新裁图或修改文字后，已有译图文件保留，但不再显示；可单独重新生成译图。

`config.json` 的 `figure_images.enabled` 开启译图，`provider` 默认为 `codex`，通过独立的 `codex exec` 调用内置生图，强制 ChatGPT 登录、忽略用户 API 配置，并移除子进程中的 API Key 环境变量。该步骤与仅返回文字的翻译调用分开；在临时目录接收并验证图片后，再由翻译流程写回文献库。`provider: "api"` 仅在用户主动选择图片 API 时使用，不作为 Codex 失败后的自动退路。

`POST /api/p/<id>/figure-images` 创建只处理图片的任务，提交和开始执行时均要求 `figure_images.enabled` 开启。阅读页从 `state/versions` 读取 `figure_images_enabled` 控制操作入口，设置保存后立即更新，其他标签页通过轮询同步；已有译图的展示不受此开关影响。省略 `ids` 时选取当前论文所有已有原图的图块；`ids: [块id, …]` 指定单张或多张图片，`force: true` 强制重做已有有效译图。任务沿用论文队列、互斥和取消功能，`job.type` 为 `figure-images`，`figure_ids` 保存选定范围，`image_failed` 按块 ID 记录失败原因，`done/total` 单位为张。本文入口默认补齐，单图入口默认强制重做。已在执行的译图任务遇到服务重启时标记中断，保留已保存的结果，等待用户手动补齐，避免未知远端结果被自动重复调用；尚未开始的排队任务可继续。

浮动体（表、图）放在正文第一次提到它的段落之后，`page` 仍写它实际所在页。

新增一批块（`easyread blocks ID --from 批次.json --done 4-6`）时可带 `"_after": "某块id"` 指定插入位置，否则追加到末尾；同 id 的块整块替换。

### 行内标记（zh、en、单元格、讨论正文通用）

- `$...$` 行内公式（KaTeX）；字面美元符写 `\$`
- `**粗体**`、`*斜体*`、`` `代码` ``
- `[7]`、`[2, 5]`、`[VSP+17]` 自动链到对应的参考文献；可唯一对应首位作者与年份时，`Smith et al., 2020` 也能跳转。条目缺失或对应不明确时保留原文。
- `[文字](#cite:键)`：作者-年份式、字母标签式的文献引用，由翻译的模型按原文写，键和参考文献条目的 `id` 同一个写法，例如 `[Jimenez 等人（2024）](#cite:Jimenez-2024)`。比对时不看大小写、标点和重音；对不上时按“第一作者-年份”找，只有一条才链。找不到只显示文字
- `[文字](#类型:编号)`：指向本文图、表、公式、章节、附录、算法、代码清单的链接，由翻译的模型按原文写在 en 和译文里，例如 `[图 3](#fig:3)`、`第 [2.1](#sec:2.1) 节`、`式 [(4)](#eq:4)`、`[附录 C.1](#sec:C.1)`、`[代码清单 3](#lst:3)`。类型 `fig`/`tab`/`eq`/`sec`/`alg`/`lst` 分别对到 `num` 相同的图、表、标题（含附录），`tag` 相同的公式，`role` 为 `algorithm`/`listing` 且 `num` 相同的 para 块；找不到目标（比如还没翻译到）只显示文字。带链接的 PDF 在给模型的材料里把链接标成 `[文字](#)`，提示这里是引用。问 AI 的回答也这样写
- `[^13]`、`[^*]`、`[^†]`：脚注标记（数字或符号，`∗` 当 `*`），链到同页 `role: "footnote"`、`num` 相同的块
- 讨论正文里空行分段；单独一段 `$$...$$` 是行间公式

JSON 里 TeX 的反斜杠要写两个（`\\frac`）。`\f` `\b` `\t` `\n` `\r` 开头的命令（`\frac`、`\bar`、`\text`、`\nu`、`\right`）写错会被 JSON 悄悄吃掉，`paper.py check` 会报“含控制字符”。

## discussion.json

```json
{ "schema": 2, "entries": [
  { "id": "d001-ab12c", "anchor": "s2-1-p4", "quote": "对它（即“真实”的平均评测分数）进行推断",
    "kind": "check", "title": "原句漏了一个符号", "body": "…", "at": "…" }
]}
```

| 字段 | 说明 |
|---|---|
| `anchor` | 块 id；不写表示整篇（显示在题头旁） |
| `quote` | 可选，锚点块**译文**里的一段原话（纯文字，不能含 `$` 公式），页面会给它加下划线 |
| `kind` | `explain` 解释 / `qa` 问答 / `insight` 感悟 / `reply` 回复用户问题 / `check` 原文核对提示 |
| `title`、`q`、`body` | 标题、问题（问答用）、正文（必填，支持行内标记） |
| `reply_to` | 回复用户笔记时填笔记 id（`easyread status` 里能看到），锚点自动跟随那条笔记 |

用 `easyread discuss ID --from 文件.json` 追加（数组或单个对象）；带已有 `id` 是修改；`--delete ID` 删除。`id`、`at` 不写会自动生成。

## reader.json（只读）

```json
{ "rev": 12,
  "edits": { "s1-p3": { "zh": "用户版本", "base": "改时译者稿的哈希", "at": "…" },
             "tab1#caption": { … }, "s1-recs#2": { … } },
  "notes": { "n…": { "id": "n…", "anchor": "s1-p2", "key": "s1-p2", "quote": "…", "prefix": "…", "suffix": "…",
                    "kind": "note | question | highlight", "color": "yellow | green | blue | pink",
                    "body": "…", "created": "…", "updated": "…", "deleted": false } },
  "paper_note": { "body": "整篇的论文笔记", "at": "…" },
  "progress": { "block": "s3-1-p2", "off": 0.4, "ratio": 0.35, "at": "…" } }
```

读到哪儿：`block` 是视口上部 30% 那条线穿过的块，`off` 是那条线在这个块里占的比例（块有几屏高时靠它落回原处），`ratio` 是全文比例，文献库列表上的百分比用它。1.4.2 及以前只有 `block` 和 `ratio`。

编辑的键：普通块是块 id，表/图题注是 `id#caption`，列表项是 `id#序号`。页面只通过 `/api/p/ID/ops` 发操作（`edit`、`note`、`note_del`、`paper_note`、`progress`），每个操作幂等、带时间戳，同一对象以较新的为准；服务端加锁、原子写、记日志。离线版导出的修改用 `easyread merge ID --from 导出.json` 并回。

智慧标注在 `ai_annotations` 中保留 `source_quote_en` / `source_quote_zh` 与来源哈希，正式笔记通过 `ai_annotation_id` 关联。接受操作在读者写锁内用当前 `paper.json` 和 `reader.edits` 重新验证；有用户修订时不沿用原稿句子对齐。重定位优先当前译文，其次可靠原文；失败时保留已验证的来源哈希，不以近似文本替代。笔记正文、颜色、删除标记及用户更新时间不因来源刷新而被覆盖。AI 笔记的 `quote` 可含来源 Markdown/TeX，浏览器仅转换定位视图，不改写保存的摘录。

## item.json（只读）

```json
{ "added": "…", "tags": ["统计"], "status": "unread | reading | done", "starred": false,
  "last_opened": "…", "meta_override": { "title_zh": "…" } }
```

## job.json（后台任务状态）

新增 `phase: "prepare" | "model"` 表示当前阶段；`type` 仍为用户请求的 `prepare / translate / read / figure-images`。因此 `type=translate, phase=prepare` 表示已预约翻译、但还在准备 PDF。`state=queued/running` 分别表示该阶段的等待/执行，其他状态仍沿用原有含义；需要页数确认时为 `confirm`。`translate=true` 表示准备完成后有模型任务，`read=true` 表示只整理原文。文献库摘要也返回 `phase/translate`。

`allow_auto_translate` 为单任务自动接续许可：批量导入为 true，明确只导入、公开 API 或研究导入为 false；停止或取消任务也写 false。旧任务缺字段时视为未授权，即使开启全局 `auto_translate_after_prepare` 也不自动接续。明确的 `/prepare` 重试也写 false，以保持仅预处理、不消耗模型额度的约定。全局开关默认 false，仅在成功预处理结束时检查，禁用引擎时不自动排队，不追补已完成历史任务。成功交接一次性写入 `type=translate, translate=true, phase=model, state=queued`。API 的 `auto_translate_pending` 是按当前配置计算的界面字段，不写入 job.json。运行任务取消时先保存 `cancel_requested=true` 再通知 worker；重启后将此类未完成任务置为取消，不恢复模型调用。新的明确请求重置该标记。

PDF 预处理独立单 worker；模型阶段沿用 `paper_concurrency`。预约优先于普通预处理，同优先级在当前进程内 FIFO，不抢占执行中的任务。请求参数（页码、范围、模型、语言、额外要求）与预约一同落盘；交接和取消在调度锁下完成。同一篇没有两个并行阶段。老版没有 `phase` 的未完成任务，启动时按是否需要模型及已保存的 `meta.pages` 分队列恢复，不自动启用翻译。预处理时间另存 `prepare_timing`；模型阶段的 `timing` 不包含排队时间。

`{"type": "translate", "state": "queued | running | done | partial | error | cancelled", "message": "…", "done": 4, "total": 14, "error": "", "failed": {"7": "原因"}, "scope": "all | body | first:N"}`。服务重启后 queued / running 的任务会自动继续。`partial` 表示有页没译成功（`failed` 里是页码和原因），页面上可以一键重试。每篇的翻译过程记在 `job.log`。

## layout.json（自动生成的原页定位）

```json
{ "s1-p3": {
  "page": 1, "src": "text", "box": [0.08, 0.10, 0.92, 0.90],
  "boxes": [[0.08, 0.60, 0.48, 0.90], [0.52, 0.10, 0.92, 0.30]]
} }
```

每个坐标框都是按原页宽高归一化的 `[x0, y0, x1, y1]`。跨栏段落的可选 `boxes` 保存各栏的独立区域，按从左到右排列；原页高亮和点击定位逐个使用这些区域，保留栏间空白。`box` 保留整体外接框以兼容旧数据；没有 `boxes` 时只使用 `box`。图表仍使用包含图像的完整区域。

服务首次打开已有文献时会用本地字符坐标重算定位，无需重新翻译；离线导出会包含同样的定位数据和高亮逻辑。

## chat.json（“问 AI”的对话记录，只有服务写）

```json
{ "threads": [
  { "id": "t…", "title": "我标红的那些公式有什么联系", "model": "opus", "created": "…", "updated": "…",
    "messages": [
      { "role": "user", "content": "…", "anchor": "s1-recs", "quote": "", "note": null, "at": "…" },
      { "id": "m…", "role": "assistant", "content": "……", "model": "Claude Opus 5", "anchor": "s1-recs", "note": null, "at": "…" } ] } ] }
```

一篇论文可以有多个对话。`note` 不为空时，这次是在回答页边那条笔记里的问题，回答同时写进 discussion.json（`reply_to` 那条笔记，`live: true`）。“放到页边”把一条回答写成 discussion.json 里的 `qa` 条目。提问时会把读者的全部标记（按颜色分组）一起交给模型。

## 数据目录里的其他文件

全库问答的段落索引仅保存在服务进程内。下一次查询按当前有效文献列表和 `paper.json`、`reader.json` 的版本重用或重建段落，移入回收站的文献会移出索引。每次问答的段数和模型报告的输入、输出 token 记在服务日志里。

| 文件 | 内容 |
|---|---|
| `config.json` | 设置：翻译引擎、各家 API Key（`openai.keys`，按服务商分开存）、问 AI 的模型名单和默认模型（`chat.models`、`chat.default`） |
| `prefs.json` | 界面偏好：阅读页字号、版心、主题、划线笔（`reader`），功能开关和快捷键总开关（`ui`），改过的键位（`keys`） |
| `easyread.log` | 服务日志，设置底部“查看运行日志”能看到 |

## 图：裁图和图内文字

- `box` 是模型给的图本身的归一化裁剪框 `[x0, y0, x1, y1]`，不含题注。翻译和只读原文整理都在合并锁里、块 id 最终去重之后才按 `box` 裁图，所以并发批次撞了 id 也不会指到别人的图
- 截图文件名是 `figures/crop-<页>-<哈希>.webp`，哈希包含页码、裁剪框和 PDF 文件身份：重译改了框会换新文件，不会沿用旧图或浏览器缓存。裁图失败就清掉 `src` 和 `box`，保留原页入口
- 没有 `box` 的图块（模型没给，或旧论文）：打开阅读页时后台按原页定位（layout.json）里的图区域补截，这个区域包含题注
- `image_en` 是图内可读文字原文，`image_zh` 是目标语言的译文（字段名沿用历史叫法），在图下单独显示，不改图片像素。只读原文先显示 `image_en`，补译时按 `id#image` 写入，编辑和重译用同一个键
- 旧论文不会自动调模型补提取图内文字；要重新整理对应页，或由 agent 核对原页后补上

## 为什么用 JSON 文件而不是数据库

EasyRead 是个人工具：一个人、一台电脑、几十到几百篇论文。每篇一个文件夹、几个 JSON，好处是能直接看、能直接备份和同步（网盘、git 都行），agent 在对话里也能直接读写；按“谁写哪个文件”分开之后，也不需要数据库的并发控制。文献库列表每次扫描各文件夹生成，几百篇以内是毫秒级。浏览器 localStorage 只用来暂存还没写进文件的修改和缓存偏好，不是数据的正本。

## 独立研究项目（1.6）

- `<文献库>/workspace/<20位随机小写英文ID>/` 是原生 Agent 的实际 cwd，包含用户文件、README、AGENTS/CLAUDE 指令、`.git/`、`.beads/` 和可选 `latex-workspace/`。改显示名称不改变 ID 或目录。
- `<文献库>/.research-workspaces/<ID>/project.json` 保存 `id/name/description/engine/created/updated/papers/initialization`，`papers` 只保存原文献 ID。不存在向研究项目复制论文正文、PDF、笔记或批注的迁移。
- 同一私有元数据目录的 `sessions/journal.sqlite` 保存界面消息投影、追加事件和小型会话摘要（SQLite WAL）。首次读取一次性导入旧 `sessions/state.json`，保留原文件但不再更新；恢复时不要把该旧文件当成当前备份。备份应在停止工作后复制完整元数据目录或使用项目导出，不可漏掉尚未 checkpoint 的 WAL。
- `sessions/native/{codex,claude}/` 仍保存私有原生状态，单独于可编辑工作目录。原生状态是续聊和 Goal 状态的正本，浏览器消息不能代替 native thread resume。
- 界面消息使用稳定 `id`，事件使用单会话单调 `seq`，增量接口游标覆盖文本和状态/待回复变更。界面仅保存有界窗口，完整历史没有因分页被删除。待回复请求携带 `generation` 与原生请求 ID；重启/导入后的旧句柄标记 expired，不能作为有效运行通道重放。
- 归档内部 `state/manifest.json` v2 保存完整项目级持久状态清单/校验值：对话、子会话、任务、文件历史、记忆与支持的 Codex SQLite 数据库。导入新项目保留原生 UUID，并迁移 cwd、原生目录和数据库中的路径/正文偏移。旧 v1 对话归档仍可读取。宿主认证/配置、环境快照、诊断日志、运行锁、可重建全局缓存和外部服务状态排除；未知布局/数据库 schema 明确报错，不静默丢弃。普通删除不是对 Git/原生数据库历史的安全擦除。
- 浏览器草稿、选中文件/会话与面板比例按 `library_id + project_id` 隔离；浏览器草稿不是服务端文件的正本。写文件携带读取时的内容版本，冲突返回 409，用户可保留草稿另存副本。
- `latex-workspace/.easyread-latex.json` 保存主文件、引擎及编译状态；输出、日志、源码快照位于 `.easyread-build/`。每次成功的 SyncTeX 构建在 PDF 旁保存 `.synctex.gz` 与 `.easyread-sync.json`（源码/PDF/映射哈希及页尺寸），供当前版本双向定位，陈旧版本拒绝映射。只有显式编译才运行 TeX。
- 新问答轮次给用户消息、助手消息与工具事件添加共同的 `turn_id`，便于逐轮折叠显示；旧记录保留，不做强制迁移。
- `.easyread.zip` 是带 `manifest.json`（`format=easyread-research-workspace`、`schema=1`）、`workspace/` 和项目会话 `state/` 的版本化 ZIP。导入生成新项目身份，保留引用 ID；不执行 hooks、Beads 配置或编译。完整文献库另行备份。

安装、信任边界和依赖升级见[研究空间](research-workspaces.md)。

## 研究工作区与画布批注

PaperRead 迁移增加以下数据，不改变现有论文 JSON 的字段含义，也不迁移或重命名 EasyRead 数据目录：

- `reader.json` 增加 `ink`、`stickers`、`ai_annotations` 字典。对象保留稳定 ID、更新时间与删除标记；手写保存段落锚点、参考尺寸、文字哈希和点集，贴纸/文本框保存位置、素材及可选 `groupId`。操作继续通过读者写锁、日志与本机待发送队列处理。
- `item.json` 增加 `reading_plan`、`interested`、`paper_ref`、`manual_order` 等研究字段。版本轮询包含 `item`，`/api/p/{id}/part/item` 可读取对应最新数据。
- `<文献库>/.paperread-planning.sqlite3` 保存候选、阅读状态、里程碑、关注、摘要与阅读路线。沿用上游文件名以兼容其数据；这与论文正文的 JSON 存储分工不同。路线和导图带版本号，陈旧页面的写入不能覆盖较新的结果。
- `<文献库>/.research-tasks/` 保存后台研究任务状态。服务重启后标记中断任务，不静默重复模型调用。
- EasyRead 数据目录 `.research-os/` 保存研究提案、检索模型缓存和按 Vault/文献库隔离的 SQLite 索引。提案绑定创建时的规范化 Vault 路径；切换 Vault 后不能把旧提案误写到新位置。
- Obsidian `Research/.paperread-index.json` 记录已导出的稳定研究 ID。Markdown 的 `paperread:managed` 区块用于安全更新；区块外的用户编辑保留。不带来源 Vault 的历史提案需重新生成后再接受。
- `<文献库>/.zotero-sync.json` 记录单向同步的 PDF 指纹，防止自动重新导入用户已删除的文献。

离线 HTML 同时嵌入已保存的导图、深度报告、画布数据和贴纸资产；它不保存模型密钥。研究工作区的使用入口和导出限制见[研究指南](research.md)。

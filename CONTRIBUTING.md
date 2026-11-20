# 参与贡献

欢迎提 Issue 和 PR。下面是一些约定，照着来能更快合并。

## 本地跑起来

需要 Python 3.10+。

```bash
git clone https://github.com/Edwardxlai/easyread
cd easyread
python -m pip install -e .
easyread
```

浏览器会打开 `http://127.0.0.1:8765`。也可以直接用 `start.cmd`（Windows）或 `./start.sh`（macOS / Linux）。

改桌面版（Electron）还需要 Node.js 22+，见[桌面版开发与打包](docs/guide.md#桌面版electron)。

论文笔记的 Tiptap 工具栏源码在 `easyread/web/src/note-toolbar.tsx`，编辑器与 Markmap 集成在 `easyread/web/js/reader/paper-note-editor.js`。修改 `src/` 后运行 `npm ci`（首次安装依赖）和 `npm run build:editor`，刷新阅读页查看效果。`npm run dist` 会先构建编辑器资源，再打包桌面程序。

macOS 开发时可用 `node --watch-path=easyread/web/src scripts/build_note_editor.cjs` 监听源码，保存后自动编译；浏览器使用强制刷新加载新的资源。

## 跑测试

用 `python scripts/preview_fixture.py --port 0` 启动独立样例库。按改动选检查可运行 `python scripts/check_change.py --base origin/main --head HEAD`，未提交改动省略 `--head`。模块归属见[开发入口](AGENTS.md)。

### v1.5 浏览器测试环境

要试用完整应用，运行 `python scripts/preview_v15.py --full --port 18825`，打开 [测试文献库](http://127.0.0.1:18825/)。它使用独立的持久测试库，包含公开 DPO 演示论文和一篇本地编写的网页样文。导入、笔记和全库问答走正常服务，默认问答使用本机 Codex，也可以在设置里更换模型。

在安装了本项目 Python 依赖的环境里运行 `python scripts/preview_v15.py`，打开 [示例论文](http://127.0.0.1:18815/read/demo-dpo) 或[模型设置](http://127.0.0.1:18815/?settings=chat)。从模型设置点「添加模型」可查看当前进程能找到的 CLI、安装指南和重新检测入口。探测只查可执行路径。需要复现 Claude 型号时可传入 `--model-cache <EasyRead 数据目录>/.models-seen.json`。预览仅导入与当前 CLI 构建匹配的型号记录。所有问答均返回本地模拟内容，可以附一份 TXT 验证文件里的短句是否出现在流式回答中。刷新后从「问 AI → 全部对话」重开记录。

用 `python scripts/preview_v15.py --cli none --port 18816` 预览未安装 CLI 的状态。另有 `one` 和 `many` 两组固定样本。每个场景与端口各用一份 `outputs/v15-preview/<场景>-<端口>/`，Ctrl+C 停止服务，同一命令重启会继续使用原有测试笔记和对话。

样本来自仓库的 DPO 公开演示，包含 27 页原页图片和段落定位。论文为 Rafailov 等人的 *Direct Preference Optimization: Your Language Model is Secretly a Reward Model*，授权 CC BY 4.0，中文译文与示例批注沿用公开试读。此环境可体验侧栏、笔记、附件与问答；原 PDF 文字层、重新翻译和安装包升级需用对应环境另验。[论文](https://arxiv.org/abs/2305.18290) · [许可](https://creativecommons.org/licenses/by/4.0/)

### 单元测试与浏览器回归

```bash
python -m unittest discover tests -v
node --test tests/web/test_*.cjs tests/desktop/test_*.cjs
```

PWA 回归使用独立样例库：先运行 `python scripts/preview_fixture.py --with-web --port 18865`，再用已安装 Playwright 的 Node 环境运行 `node tests/browser/pwa.cjs`。可通过 `NODE_PATH` 指向独立工具目录的 `node_modules`，`CHROME` 指定 Chrome 路径，`PWA_TEST_URL` 指定样例服务地址。脚本创建并删除独立浏览器 profile，检查安装条件、离线冷启动、笔记补交、网页/图片、存储失败与授权隔离；会更改样例授权码并恢复，**只允许对样例库运行，不要指向真实文献库**。Android/iOS 安装菜单和独立窗口仍需对应真机验证。

后台导入回归使用同一个独立样例服务，运行 `IMPORT_TEST_URL=http://127.0.0.1:18865 node tests/browser/import-tasks.cjs`（同样需要 Playwright/Chrome）。脚本自建本地网页源和反向代理，模拟“后端已接收但代理返回 504”，验证胶囊、多任务计数、取消、刷新恢复和手动重试；不会调用模型。两个浏览器回归请各自使用新建的样例库。

软键盘焦点回归：在具有 Playwright 和 Chrome 的 Node 环境运行 `node tests/browser/note-focus.cjs`（可用 `NODE_PATH` 指定依赖目录）。它使用真实阅读器与一次性静态样例，检查高度变化、旋转、长草稿、输入法事件、颜色/类型切换和关联图表，不连接真实文献库。软键盘与组合输入由事件模拟，仍需对应真机确认。

iPad 手写回归：`NODE_PATH=<Playwright 的 node_modules> node tests/browser/ipad-ink.cjs`，使用独立静态样例和 Chrome，检查 120 段正文、250 条既有笔迹下的独立视口预览、增量落笔、关闭开关、Android/桌面旧路径、手指转 Pencil、橡皮擦及撤销。额外通过 Chromium CDP 的可信 pen 输入命中既有 SVG 子路径，真实建立/释放捕获，在书写中交错 resize/重绘请求，并验证画布外抬笔及持续手掌隔离；不连接真实文献库或模型。平台标识、部分指针和手掌事件仍是模拟的，结果不是 iPad Safari/PWA 真机手感验证。新预览模块通过 `reader.html` 的标准脚本标签进入现有离线 HTML 内联流程；笔迹存储格式不变。真机重点核对旋转/缩放、快速抬落笔及滚动边界，确认设置 → 阅读中的「iPad 手写优化」可关闭。画布仍为应用管理的滚动，不提供原生惯性。

iPad 输入细节参考 [pencil-annotation](https://github.com/BUGdefender404/pencil-annotation) 的独立实时层、合并采样、丢失抬笔恢复与动画帧兜底思路（参考版本 `d590d3e`）。EasyRead 保留自身 SVG 曲线、段落锚点和二维笔迹格式；不引入其压感依赖、图形吸附或模拟惯性。

模型设置回归：`PYTHON=<具有项目依赖的 Python> NODE_PATH=<Playwright 的 node_modules> node tests/browser/model-settings.cjs`。脚本建立临时文献库和假 Codex（仅允许版本查询），验证缺缓存时手工型号、翻译/问答角色、条目级模式、继承和刷新恢复；不连接模型服务，不接触部署机器。

论文笔记的浏览器回归脚本在 `tests/browser/paper-note-workspace.cjs`。用 Playwright CLI 打开临时文献库中的中文阅读页后，执行 `run-code --filename=tests/browser/paper-note-workspace.cjs`，可验证中文输入法退格、图片上传／粘贴／拖入、缩放设置和导图缩略图；脚本会恢复测试笔记、缩放比例和导图开关。

全屏工作区的布局回归脚本为 `tests/browser/paper-note-layout.cjs`，在中文阅读页执行可检查左右／上下分栏、鼠标和键盘调整比例、单栏聚焦、窄屏布局及 Esc 退出。

提 PR 后 GitHub Actions 会在 Windows、macOS、Linux 上自动跑一遍。第一次贡献的 PR 需要维护者点一下批准才会开始跑，稍等就好。

## 提 PR

- 一个 PR 只做一件事，方便审和回退。
- 标题写清改动。说明可自由写，模板各节均为选填。
- 修 bug 可附复现步骤，改界面可附截图。
- 修了 bug 或加了功能，尽量在 `tests/` 里补一个测试。
- 不用改 `CHANGELOG.md` 和版本号，发版时维护者统一写。

## 界面文字

界面跟着系统语言显示中文或英文。写代码时只写中文，外面套一层：前端 `PR.t("已导入 {n} 篇", { n })`，后端 `tr("已导入 {n} 篇", n=n)`。英文写在 `easyread/web/i18n/en.json`，缺了的话测试会报出来；不方便写英文就在 PR 里说一声，维护者来补。

`python scripts/i18n_check.py` 会列出没套 `PR.t` / `tr` 的中文和缺英文的词条。确实不该翻译的中文（比如给模型的提示词），在那一行末尾加注释 `i18n-ok`。

## 报问题

直接开 Issue 就行，有模板提示要写哪些信息，填不全也没关系。

论文笔记支持从正文选文菜单引用，来源以 Markdown 段落链接保存。点击来源回到正文，正文的“关联笔记”入口可循环定位同一段落的多处引用。

`tests/browser/paper-note-quotes.cjs` 验证长引用两行折叠、展开、来源跳转及内容保留。

`tests/browser/paper-note-sources.cjs` 在临时 DPO 示例阅读页验证选文引用、全屏返回正文、反向定位及刷新后的引用恢复。

# EasyRead user guide

[Project home](../README.en.md) · [简体中文](guide.md)

External scripts and agents: see [Open API and MCP](open-api.md#english-quick-start) for document/note/highlight reads and asynchronous imports using the existing access code.

> **Choose your reading language.** Translate papers into **Simplified or Traditional Chinese, English, Japanese, Korean, Spanish, French, German or Italian** (English is for papers written in other languages, such as German or French). Choose a language when importing, or set your default in Settings → Reading → Translate papers into (initially Chinese). The interface supports **English, Simplified Chinese and Traditional Chinese**, independently of the translation language. You can also choose "Read the English original" on import and use the layout, highlights, notes and Ask AI without translating.

<p align="center"><img src="images/en/japanese.png" width="860" alt="Japanese abstract alongside the original English PDF"></p>

## Install as a PWA and read offline

Choose **Install / Offline** in the library or reader. Android Chrome and desktop Chrome/Edge offer browser installation. Android Edge behavior varies by version/device; a normal website shortcut is not a standalone app. On iOS 16.4+, use **Share → Add to Home Screen** in Safari/Chrome/Edge; use Safari on older systems or when the option is missing. The manifest requests `fullscreen`, with the standard `standalone` fallback on unsupported platforms. iOS controls its own status-bar/display behavior. Fullscreen display does not prove WebAPK package installation.

- Mobile connections require **trusted HTTPS**. Localhost HTTP is allowed for development, but ordinary LAN-IP HTTP is not.
- Stay online until “The app is ready to start offline”. Recently opened text, existing translations and notes are saved automatically. This includes text extracted from PDFs, but **not original/bilingual PDFs, page images or PDF text layers**. New translations, AI, imports and research services need a connection.
- Use **Save current document for offline reading** or **Complete and pin** to retain reading figures, note images and the archived webpage. Partial-resource status means images or original styling may be missing; text remains available in EasyRead layout. External resources that could not be archived are not guaranteed offline.
- Text/resource budget defaults to 128 MB (64–512 MB selectable). Older unpinned copies can be evicted; pinned documents and pending notes are protected. Removing copies never deletes server documents. The app shell uses additional space; the manager also shows estimated total site usage.
- Notes, highlights, handwriting and progress enter a durable device-local operation queue. They sync when the corresponding reader reconnects and is authorized; reopen a closed document to sync its pending changes. Operations are removed only after server acknowledgement and local snapshot persistence. If storage fails, export your changes before clearing browser data.
- Cache is not a backup: browsers and the OS can remove it. An installed app may have separate storage, especially on iOS; check downloaded documents inside it. Offline copies cannot be instantly revoked by the server. Use trusted devices only.
- Signing out clears the current library’s offline copies on this device. Pending edits block sign-out; sync first or retain an export. Observed authorization failures never fall back to private cached content.
- To update safely, save notes, close all app windows and site tabs, then reopen. Updates never force a reload while you edit.

**A WebAPK is not a home-screen shortcut.** On supported Android devices it appears in Settings → Apps and can be uninstalled. The browser's “Install app” wording does not prove a WebAPK package was generated: Chrome/device support, Google Play services and installation policies matter. The Install / Offline dialog reports secure context, window mode and worker control, but websites cannot inspect Android's installed package list. Login, library, reader and research entries share the same stable manifest identity and icons. A standalone window still uses a browser runtime; Android WebAPKs share Chrome site storage, while iOS Home Screen storage may differ. Sync or export pending notes before uninstalling or clearing any data. See [MDN installation](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable) and [WebAPKs](https://web.dev/articles/webapks).

Electron and single-file offline HTML keep their existing behavior.

## Background link imports

Enter a webpage/paper URL, arXiv reference, DOI or title and choose the existing import options. Clicking Import closes the dialog and shows submission/import progress in the bottom-right task capsule, shared with mindmaps and deep analyses. Open it to inspect individual tasks. Completion refreshes the library without changing your selection or interrupting reading.

A 504 or lost acceptance response is shown as an unconfirmed submission, not a definite import failure. Check or retry reuses the original request ID and first looks for an accepted task. Input/options remain in the tab session. Accepted tasks reappear after reload or navigation through server status; the browser never automatically reposts imports.

Cancel removes queued work and cooperatively stops running fetches between steps; a current network request may need to finish or time out. Saved documents remain available. Import completion means the document is saved and subsequent processing is scheduled—not that translation is finished. If optional PDF generation cannot start, retry it separately from the task details using the captured model/folder, without repeating the import or reader translation. Later PDF failures remain retryable in document details. Check the library before manually retrying tasks interrupted by a server restart.

Task state and source links are stored locally in the current library behind the existing access protection. Dismissing a notice does not cancel server work. File uploads, Zotero/folder imports and the public API retain their existing behavior.

## How it differs from "throw the PDF into a translator"

- **Reads like a typeset book.** Serif body text, comfortable line length and spacing. Equations are re-rendered with KaTeX, tables become clean three-line tables, references stay in the original, and bold and italic in the original carry over to the translation. One click in the top bar for dark mode.
- **The original is always right there.** Toggle "bilingual" to show the English under each paragraph. Open the original page on the right: it follows your reading position and draws a box around the paragraph you're on.
- **Find text in a paper.** Press Ctrl / ⌘ + F to search both the original and the translation, with a match count and highlighted results. Enter / Shift + Enter moves to the next / previous match.
- **Translation and commentary are kept apart.** The main text is only the faithful translation. AI explanations and answers go in the margin, so you always know what the paper actually says.
- **Ask AI while you read.** A chat panel with streaming answers. Quote several paragraphs at once (drag selected text into the input). Ask "how do the equations I highlighted in red relate?" and it finds your red highlights. Multiple chats, each with its own model: Claude, GPT (Codex), DeepSeek, Qwen, local Ollama… Pin a good answer to the margin in one click.
- **Annotate.** Four highlighter colors or underline, notes, questions. Send a question to AI with one click, or ask it to comment on a note. The notes panel collects annotations in reading order; filter and scroll through them. Clicking a card jumps to the passage; use Edit to change the note. Attach figures and tables by choosing thumbnails or dragging them from the paper, then expand them or edit their names. Hover over figure and table references in the text to preview them. Export selected annotations to Markdown (Obsidian, Notion).
- **Edit the translation.** Double-click a paragraph to edit it; change a term in the glossary and it's replaced everywhere.
- **Not just arXiv.** Drop in any PDF, or paste an arXiv ID, DOI, paper title or paper page (OpenReview, ACL, NeurIPS, bioRxiv, PMC, journal sites). It finds the open PDF and fills in authors, year and venue.
- **Move over from Zotero.** In the import dialog, "Move your whole Zotero library" finds your Zotero data folder and brings over every paper with a PDF, along with its collections (nested ones stay nested), title, authors, year, venue, DOI and abstract. It only reads a copy of the Zotero database and never changes Zotero; you can run it again later. "Import a whole folder" does the same for a folder of PDFs: subfolders become nested folders in the library. Both run in the background and can be stopped. They only prepare PDFs by default; model settings offer automatic translation after preparation.
- **A library of your own.** Pin papers and folders, create folders and drag papers into them, nest folders (right-click → "New sub-category" / "Move to…"; a parent folder lists everything inside it), recent reads, search, unread / reading / done, stars, reading progress, copy citation (GB/T 7714, APA, BibTeX), export a single-file offline HTML to share, or "Export for phone" (read-only and small, open it in a phone browser). Deleted papers go to a trash you can restore from. Custom keyboard shortcuts.
- **Cloud library and batch citations.** Move your library to another drive or a synced folder in Settings → Cloud library; your cloud drive client handles syncing. Copy or save citations for a paper list or folder in GB/T 7714, APA or BibTeX.
- **ASD-STE100 Chinese answers.** Choose this answer style in Ask AI for concise Chinese explanations that retain technical terms, equations and conditions. It borrows writing principles from the English standard; it does not certify Chinese answers as compliant.
- **Close reading answers.** The third answer style in Ask AI, for when you want to know why the authors did something and how far a result really holds: it reconstructs the problem they set out to solve, separates load-bearing design from decoration, works key formulas through with concrete numbers, and checks reported numbers and baseline conditions. Anything the paper does not report is called out rather than filled in. Like the concise style, it sends the whole paper as context and uses more quota.
- **Know what you're spending.** Every translation and every AI answer records its token usage; with a Claude subscription you also see how much of the 5-hour / 7-day quota is used and when it resets.
- **Doesn't lose your work.** Every edit is saved in the browser first and only cleared once the local server confirms it's on disk. If a re-translation touches a paragraph you edited, you get a notice, not an overwrite.

<p align="center"><img src="images/en/chat.png" width="860" alt="Ask AI while reading"></p>

<p align="center"><img src="images/en/library.png" width="860" alt="Library"></p>

## Phone and tablet reading

- Long-press text and adjust the native selection handles, then use the bottom annotation toolbar. The system still owns Android's native copy menu. Toolbar taps preserve the quote. Paragraphs, archived webpages and editable offline HTML share this flow; the deliberately read-only mobile HTML export remains read-only.
- **Aa → Note layout** offers Auto, Expand below and Numbered pins. Auto can show margin notes from roughly 960 CSS pixels when font size and panel space permit; larger text or an open side panel falls back to pins. Expand below displays paragraph notes directly on narrow screens. The preference is saved. Native webpage layout still opens notes from its markers; switch to EasyRead layout for inline/margin notes.
- Touch controls have larger targets and labeled eraser/lasso actions. Done exits handwriting. Floating installation/status UI avoids annotation controls and screen safe areas.

## Handwriting and stickers

Handwriting and stickers open independently: opening the sticker library closes handwriting, without enabling the pen. Handwriting defaults to mouse/touch input. Click its input-mode button to select pen-only input; the choice is saved in this browser. In pen-only mode, drawing, erasing, lasso selection and text placement accept only pointers the browser identifies as `pen`. Mouse drawing is disabled, but a finger can drag the canvas to scroll between pen contacts; wheel, trackpad and scrollbars remain available too. Canvas finger scrolling is application-managed: touch-action:none is retained so browsers cannot turn pen strokes into page panning. It is not native inertial scrolling and does not provide two-finger canvas zoom. While the pen is down, accidental input, text selection and menus are suppressed, and any current finger drag ends. After lifting the pen, a fresh finger or mouse gesture can configure or exit; the keyboard remains available. Enabling handwriting switches native web articles to the existing EasyRead paragraph layout; switching back closes handwriting. Closing handwriting restores normal reading and sticker interaction. If your device reports its pen as a mouse, deliberately switch back to mouse/touch mode, which does not offer palm rejection. Chrome is recommended for graphics tablets on macOS: if Safari reports only `mouse`, the page cannot reliably distinguish a tablet pen from a mouse and does not guess from pressure. Safari requests an annotation/sticker repaint after scrolling settles; Chrome keeps its existing rendering path.

## Pick your translation model

| Engine | What you need | Notes |
|---|---|---|
| **Claude Code** (recommended) | [Claude Code](https://docs.claude.com/en/docs/claude-code/setup) installed and logged in | No API key, uses your subscription. Looks at the page image to check equations. Best quality |
| **Codex CLI** | [Codex](https://github.com/openai/codex) installed and logged in | No API key, uses your ChatGPT account |
| **CodeBuddy CLI** | Install and sign in using the [official CLI docs](https://www.codebuddy.cn/docs/cli/headless); your account needs CLI access | No API key. Type the model name, or leave it empty for the CLI default. [The current free trial does not include CLI access](https://www.codebuddy.cn/docs/workbuddy/Pricing); credits are shared with WorkBuddy on the same account |
| **ZCode CLI (experimental)** | Install and sign in using the [official CLI guide](https://github.com/zai-org/ZCode/tree/main/apps/zcode-cli/docs/usage), with `zcode` available on PATH | Reuses the local login and model settings. Supports translation, Ask AI and image attachments when the selected model supports them. Real-account calls still need verification |
| **Grok Build / Antigravity CLI / Cursor CLI** | [Grok Build](https://x.ai/cli), [Antigravity CLI](https://antigravity.google/docs/cli) or [Cursor CLI](https://cursor.com/docs/cli/installation) installed and logged in | No API key, uses that subscription. Type the model name, or leave it empty for the CLI's default |
| **API · China**: DeepSeek / Zhipu / Alibaba Bailian (Qwen) / Kimi / SiliconFlow / ModelScope | API key | Zhipu GLM-4.7-Flash and SiliconFlow small models are free; DeepSeek costs cents per paper |
| **API · International**: OpenAI / Anthropic / Gemini / OpenRouter / Groq / Cerebras | API key | Gemini, OpenRouter, Groq, Cerebras have free tiers |
| **API · Local**: Ollama / LM Studio | [Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai) | Fully offline. qwen3.5:9b recommended (4b for small GPUs) |
| **API · Custom endpoint**: any OpenAI-compatible API or relay | URL + key | Both Chat Completions and Responses formats; save named endpoints for reuse, with a separate key for each URL; "Fetch model list" pulls model names from the endpoint |

Settings auto-detect what's installed; "Test one sentence" tells you right away whether an engine works. A failed page (rate limit, network, quota) is retried automatically, then skipped so the rest keeps going, and you can retry all failed pages in one click at the end.

**Custom Codex model catalogs:** Translation and Q&A model selectors prefer `model_catalog_json` in `$CODEX_HOME/config.toml` (default: `~/.codex/config.toml`). Relative paths are resolved from that configuration file's directory. Without this setting, EasyRead reads `models_cache.json` from the same directory. A custom catalog replaces the cache and supplies visible models, ordering, and reasoning levels, including `ultra` where supported. After editing the catalog, click "Rescan" to refresh EasyRead's list. An empty, missing, or invalid custom catalog does not restore stale cached models. Detection only reads local files; it makes no model requests and does not modify Codex configuration.

Papers are translated one at a time by default, with parallel batches still available within each paper. In Settings → Models → Translation, choose 1–4 concurrent papers and restart EasyRead to apply. Paper concurrency multiplies batch concurrency, increasing memory use and the risk of model rate limits; keeping the default of one paper is recommended. See the [configuration notes](paper-concurrency.md).

**Separate preparation and translation queues:** PDF rendering and text extraction have their own single worker and do not consume translation slots. Prepared papers can translate while another PDF is being prepared. Click “Schedule translation” in the paper details or reader, or use batch Translate, before preparation finishes. Requested papers receive preparation priority without interrupting the current paper, then automatically join the translation queue. Each paper must still finish preparation before translation starts. The UI distinguishes waiting/preparing from waiting/translating.

**Automatic translation after preparation (optional):** In Settings → Models → Translation settings, enable “Automatically queue translation after preparation”. Zotero/folder tasks explicitly permitted to auto-translate join the end of the translation queue after successful preparation, retaining their model, language and page scope. The switch defaults off and takes effect for unfinished preparations when saved; it does not backfill previously completed papers. Translation uses model quota and still requires confirmation above the page threshold. Legacy tasks without an explicit permission field, explicit import-only choices, public API `translate=false`, and research imports remain preparation-only. The reader’s preparation retry always prepares only; explicitly choose Reserve translation or Translate when inference is wanted.

Cancel on a paper cancels its preparation and reservation. Stop model tasks also opts pending automatic translations out while letting PDF preparation finish; completion or restart will not revive them. Failed preparation does not start translation; retry after resolving the error. Restarting resumes unfinished jobs and retains saved translations. Turning the automatic switch off prevents future automatic handoffs, but does not cancel already queued translations or explicit reservations. Stop affects existing requests only; a continuing bulk import can enqueue new work, so also stop the import or disable automatic handoff if needed. Cancellation intent for running model work is persisted before signaling, so restart does not resume a cancelled request.

## Install

**Easiest: download the installer** (no Python needed). From [Releases](https://github.com/Edwardxlai/easyread/releases/latest):

- **Windows**: `EasyRead-Setup-x.x.x.exe`. It is not code-signed; if SmartScreen says "Windows protected your PC", click "More info → Run anyway".
- **macOS** (Apple silicon): `EasyRead-x.x.x-arm64.dmg`, drag EasyRead into Applications. If macOS blocks the first launch, follow the matching instructions below.
- **Linux**: `EasyRead-x.x.x.AppImage`, `chmod +x` and run it.

Where papers and settings live: the Windows installer keeps them in a `data` folder inside the install directory (kept on updates and uninstall); macOS and Linux use the `EasyRead` folder in your home directory (same place as the pip install). Reinstalling keeps them.

Updating: the Windows and macOS apps and a directly launched Linux AppImage update in-app. When a new version is out the top bar shows it (or check from the question mark at top right); click "Update", let it download in the background, then click "Update and restart". An extracted AppImage or a source install downloads the new version from Releases. macOS and Linux users on 1.3.2 or earlier need to download 1.3.3 manually once; on macOS, keep EasyRead in Applications for in-app updates to work.

### First launch on macOS

1. **“Developer cannot be verified” or “Apple cannot check it for malicious software”**: click **Done** (not **Move to Trash**), open **System Settings → Privacy & Security**, scroll down to the security message, and click **Open Anyway**. Click **Open** in the confirmation dialog, then enter your login password or use Touch ID when prompted.
2. **“EasyRead is damaged and can’t be opened”**: download it again from the [official EasyRead Releases](https://github.com/Edwardxlai/easyread/releases/latest) and drag the app into Applications. This warning can also mean the file is damaged or has been modified; it does not identify the cause by itself. Only after confirming that your copy came from that official repository and that you trust it, run the following in **Terminal**, then open EasyRead again. This removes the quarantine attribute; it does not repair a damaged file.

   ```bash
   xattr -dr com.apple.quarantine /Applications/EasyRead.app
   ```

3. **Why verification may fail**: the macOS package is signed with the project's own self-signed certificate (so in-app updates work), not an Apple developer certificate, and is not notarized, so macOS may block it. The code is open source; you can also [run from source](#run-from-source).

If macOS says the app **“will damage your computer”**, or explicitly detects malware and asks you to move it to the Trash, stop the installation and do not use the steps above to bypass the warning. Follow [Apple’s official guidance](https://support.apple.com/en-us/102445).

<a id="run-from-source"></a>

**Or run from source** (needs Python 3.10+): download the latest zip from [Releases](https://github.com/Edwardxlai/easyread/releases/latest) and unzip it (or `git clone` this repo).

**Windows**: double-click `start.cmd`. The first run sets up the environment (about a minute); after that it opens right away.

**macOS / Linux**: in the unzipped folder, run

```bash
./start.sh
```

When started this way, the background service quits on its own about 15 seconds after you close every EasyRead page in the browser; if a translation is still running, it waits until that finishes.

**Or with pip** (data goes to `~/EasyRead`):

```bash
pip install git+https://github.com/Edwardxlai/easyread
easyread
```

Your browser opens `http://127.0.0.1:8765`. By default, the server listens only on localhost.

**Docker / server deployment:** One container and port include Claude Code, Codex CLI, Chromium and BabelDOC. Use **Settings → CLI & terminal** for embedded login/updates, **Access protection** for an optional access code, and **Network & proxy** for global proxies. No browser username/password dialog or second terminal URL. See the [full Docker guide](docker.en.md).

## CLI capabilities for AI Q&A

In Settings → Models, open a Claude Code / Codex card menu and choose **AI Q&A · CLI capabilities**. Default lean behavior is unchanged; choose native full mode or customize tools, Skills and MCP, with Q&A-only Skill imports. The two CLIs are configured independently; translation and other automatic processing are unchanged. See [Q&A CLI settings](qa-cli.en.md).

## Usage

**Read the PDF beside your notes.** Open the sidebar and use **+** to add Notes. Drag the outer edge all the way left to cover the reading area. Choose **Split side by side** under **+**, or drop the Notes tab on the right edge. Drag the divider to resize the panes. Drop a tab near the top or bottom to stack panes. Close the sidebar to resume reading.

**Search across papers.** Choose **Ask the library** and describe what you remember. Your default Ask AI model suggests search terms, then receives up to eight matching passages from the local library. Select a citation to open its source. Results show the passage count and any token usage the model reports. Scans need extracted text before they can be searched.

**Web articles and deployment.** Paste a blog URL to read, annotate and export the archived page. Switching between the original page and EasyRead layout preserves the current paragraph and its offset where possible. Switching before the initial page resources finish loading starts at the top instead of jumping to the hidden reader’s last paragraph. Dynamic pages need Chrome or Edge. [Web article details](web-articles.md). Deployments can set `EASYREAD_TARGET=zh-Hant` to default to Traditional Chinese. A saved language preference takes precedence.

1. Open Settings (top right) → Model: add the models you want and choose "Use for translation" on one card. Models for translation and Ask AI are managed on the same page.
2. Drag a PDF into the window, or paste an arXiv ID, arXiv / OpenReview link or a direct PDF link (`Ctrl+V` on the library page works too). For long papers you can translate just the main text, or a page range such as pages 5–12. You can also pick the model and target language for this import, or choose "Read the English original" to lay out the paper without translating; use "Translate to…" any time later. Whole-paper jobs above 60 pages ask for confirmation by default; change the limit in Settings → Model.
3. Translation runs in the background page by page. Translated parts are readable immediately; untranslated pages show the original. Importing more than 5 PDFs at once asks before translating them all (the default is "Import only"); "Stop translation" at the top of the list stops every running job and keeps what is already translated.
4. Click a paragraph for its action bar; select text to highlight, note or ask. Shortcuts are listed and editable under Settings → Shortcuts; the question mark at top right has help and updates.

## Reading with an AI agent

EasyRead ships with a CLI, so agents like Claude Code or Codex can read your notes and questions in a conversation, write answers next to the right paragraphs, or translate / re-translate pages themselves. The skill is in [`skill/paper-reading/SKILL.md`](../skill/paper-reading/SKILL.md); put that folder in `~/.claude/skills/` or `~/.codex/skills/`.

To have an agent explain a paper in depth rather than only translate it — reconstruct where the authors actually started, make the key formulas concrete enough to work through by hand, then check where the conclusions stop holding — use [`skill/paper-xray/SKILL.md`](../skill/paper-xray/SKILL.md). It reads the same `library/<ID>/` and writes its findings as discussion entries anchored to the paragraphs they belong to.

```bash
easyread list                          # list the library
easyread import paper.pdf              # or an arXiv ID / link
easyread status ID                     # progress, your edits, notes, open questions
easyread discuss ID --from answers.json   # write discussion into the margin
easyread export ID                     # export single-file offline HTML
```

See `easyread --help` for all commands and [docs/data-format.md](data-format.md) for the data format.

## FAQ

**Does it cost anything?** EasyRead is free and open source. Translation uses your own model: Claude Code / Codex use your existing subscription, local and free-tier models cost nothing, paid APIs bill per use.

**Where is my data? Is anything uploaded?** By default, files stay on your machine: `data\library\` inside the install folder for the Windows installer (1.3.1 and earlier used `~/EasyRead`; the first launch after upgrading moves it over), `library/` in the project folder when run from source, `~/EasyRead/library/` after pip install. One folder per paper holds the original PDF, page images and JSON files. Settings → Cloud library can move the library to your synced folder; your cloud drive client then handles uploads and syncing. Translation and AI questions send the relevant content to the model you chose.

**Can I read offline or share a paper?** Yes. Right-click a paper → "Export offline HTML" (or `easyread export ID`) for a single-file web page. The recipient doesn't need EasyRead: double-click to open it in a browser with the translation, equations, page images, your highlights and notes, bilingual view and the original-page panel all working. Page images are embedded, so the file is not small (about 10 MB for a 27-page paper). New highlights made in the offline copy stay in that browser; to bring them back, use "Export my changes" under About in the left drawer and run `easyread merge ID --from export.json`.

To publish on a site such as GitHub Pages, use `easyread demo ID --out DIR`: images are saved as separate files and loaded on demand, and AI chats are included (read-only).

**Can it translate into languages other than Chinese?** Yes: Simplified and Traditional Chinese, English (for papers written in other languages), Japanese, Korean, Spanish, French, German and Italian are available under Settings → Reading → Translate papers into, and in the import dialog. You can also read the English original. Interface language is independent: choose Simplified Chinese, Traditional Chinese or English under Settings → Reading → Language. Built-in reading statuses, sidebar labels, menus and the status editor follow it, including legacy stored default labels. Custom categories/status names, papers and notes are not converted. The access page initially follows the app language; its temporary language selector does not change global settings.

## Development

The reader uses HTML, CSS and JavaScript. After editing the note toolbar source, run `npm run build:editor`. See the [contribution guide](../CONTRIBUTING.md).

```bash
python -m unittest discover tests         # unit tests
node tests/e2e.cjs library/<paper-id>     # browser end-to-end test (needs Playwright and a translated paper)
```

Design notes in [docs/design.md](design.md), changes in [CHANGELOG.md](../CHANGELOG.md) (both in Chinese).

## License

MIT. Math rendering by [KaTeX](https://katex.org) (MIT).

The demo paper is Rafailov et al., *Direct Preference Optimization: Your Language Model is Secretly a Reward Model* ([arXiv:2305.18290](https://arxiv.org/abs/2305.18290), CC BY 4.0). The Chinese translation was generated by EasyRead using Claude; highlights and notes in the demo are examples.

Screenshots use a demonstration library. The Japanese sample translates the abstract; highlights, notes and the English AI answer are illustrative examples.

## Contributors

- [@Wang-auspicious](https://github.com/Wang-auspicious) — Electron desktop packaging and release workflow; the paper-xray deep-reading skill ([#40](https://github.com/Edwardxlai/easyread/pull/40)); the close reading answer style in Ask AI ([#41](https://github.com/Edwardxlai/easyread/pull/41))
- [@bisuwuss-netizen](https://github.com/bisuwuss-netizen) — missing PDF dependency in desktop builds, two-column page locating; Markdown tables and blockquotes in Ask AI and notes; model JSON parsing, academic reading prompts and parallel paper translation ([#64](https://github.com/Edwardxlai/easyread/pull/64), one paper by default, optionally up to four)
- [@MeshedPoto](https://github.com/MeshedPoto) — PDFium errors during parallel translation, cross-column page highlights, macOS desktop reliability, HTTPS certificates and streaming fixes; ASD-STE100 Chinese answers and math rendering in v1.3; figure cropping fixes in v1.3.1 ([#22](https://github.com/Edwardxlai/easyread/pull/22))
- [@Lzy22301093](https://github.com/Lzy22301093) — malformed table validation and rendering resilience ([#17](https://github.com/Edwardxlai/easyread/pull/17))
- [@NGman-s](https://github.com/NGman-s) — pages that only continue the previous paragraph no longer reported as failed translations ([#30](https://github.com/Edwardxlai/easyread/pull/30))
- [@TWwWT1](https://github.com/TWwWT1) — patches for memory blow-up when importing cropped-page PDFs ([#28](https://github.com/Edwardxlai/easyread/issues/28)) and for papers stuck loading on open ([#35](https://github.com/Edwardxlai/easyread/issues/35))
- [@kevin9327](https://github.com/kevin9327) — apostrophes in page metadata no longer truncate titles and author names ([#37](https://github.com/Edwardxlai/easyread/pull/37))
- [@Zhoushuren12](https://github.com/Zhoushuren12) — the library list no longer jumps while refreshing in the background ([#43](https://github.com/Edwardxlai/easyread/pull/43))
- [@02Ychase](https://github.com/02Ychase) — translating papers with code listings no longer fails whole batches ([#44](https://github.com/Edwardxlai/easyread/pull/44)); deleting a paper mid-translation no longer stalls the background queue ([#56](https://github.com/Edwardxlai/easyread/pull/56))
- [@88lin](https://github.com/88lin) — figures display at their size on the original page instead of always filling the column ([#47](https://github.com/Edwardxlai/easyread/pull/47))

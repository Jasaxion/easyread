# EasyRead

Read papers at your own pace. Drop in a PDF and start reading as soon as the first translated passage appears. Compare a passage with the original page, highlight it, leave a note or ask a question in the margin. You can also read the English original.

[Download](https://github.com/Edwardxlai/easyread/releases/latest) · [Try the demo](https://edwardxlai.github.io/easyread/demo/) · [Project site](https://edwardxlai.github.io/easyread/en/) · [简体中文](README.md)

![Translated text alongside the original PDF](docs/images/en/hero.png)

## Install

Choose the Windows installer (`.exe`), Apple silicon Mac disk image (`.dmg`) or Linux AppImage from the download page. See the [installation guide](docs/guide.en.md#install) for first-launch instructions.

<a id="run-from-source"></a>

To run from source with Python 3.10+, double-click `start.cmd` on Windows or run `./start.sh` on macOS or Linux.

## Docker browser version

```bash
git clone https://github.com/Jasaxion/easyread.git
cd easyread
docker compose up -d --build
```

One container, one port: open <http://localhost:8765>. Claude Code, Codex CLI, Chromium, BabelDOC and research prerequisites (Git, Beads/Dolt, three TeX engines and MCP) are included. **Settings → CLI & terminal** provides CLI login, updates and an embedded terminal.

Independent [research workspaces and LaTeX](docs/research-workspaces.en.md) provide project files, paper references and native Agent sessions for trusted private deployment. The guide covers native prerequisite checks/installation for lite, desktop and source, plus third-party version controls. Desktop packages do not bundle a TeX distribution.

No username/password browser dialog. **Settings → Access protection** enables, disables or changes a single access code with a dedicated authorization page. Protection defaults to off with a loopback-only port; enable it before network exposure and use HTTPS where needed.

Data and CLI logins persist. **Settings → Network & proxy** configures global proxies. See the [full Docker guide](docs/docker.en.md).

## Read your first paper

1. Drop in a PDF, enter an arXiv ID, or paste a blog URL. Pick a translation language or read the original.
2. For translation or questions, select a supported CLI you are signed in to, or configure a model API in Settings. Your library lives on your computer. The selected model receives the material needed for your request.
3. Open the PDF and your notes side by side. To find something you read earlier, choose **Ask the library** and follow a citation back to its passage.

Research planning, reading paths, editable AI mind maps, handwriting and stickers, deep/cross-paper analysis, split tabs and Obsidian export are integrated with the existing reader. See the [research guide](docs/research.en.md) for entry points, model requirements and data behavior.

[User guide](docs/guide.en.md) · [Research features](docs/research.en.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) · [Contributors](docs/guide.en.md#contributors) · [License and demo paper credit](docs/guide.en.md#license)

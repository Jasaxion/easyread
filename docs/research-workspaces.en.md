# Research workspaces

[User guide](guide.en.md) · [简体中文](research-workspaces.md)

**Research space** (`/workspaces`) is independent of the existing [research exploration and paper planning](research.en.md). Each project combines files, references to library papers, native Codex/Claude sessions, Git/Beads and a server-side LaTeX editor. Reading, translation and offline HTML export keep their existing behavior. Research spaces require the running EasyRead service; they are not an offline PWA editor.

## Trust boundary — read before enabling

This is a **trusted, private, single-user development workspace**, not a multi-tenant hosting service. Sending an Agent message or opening its native terminal explicitly starts a CLI with full permissions, native tools, Skills and MCP. The Agent can execute commands, modify files, access network services and use credentials available to the EasyRead service account. The project directory is its working directory, **not an OS sandbox**. Paper-reading and translation tool policies are unchanged.

Use a dedicated OS/container account, keep Docker's loopback binding unless needed, set an access code before network exposure, and use HTTPS. Do not mount your entire home, SSH directory or Docker socket. Only import projects you intend to inspect and trust. Importing or opening a project must not execute imported Git hooks, Beads configuration, Agent startup instructions or TeX. Review project configuration before explicitly starting an Agent. Exports may contain secrets that **you or the Agent wrote into project files**; review them before sharing.

## Start a project

1. Open **Research space** beside the research/library navigation. Create a project with a name, description and default Codex or Claude engine. Search by name/description, edit these fields later, or delete with confirmation.
2. Projects live under `<library root>/workspace/<random-id>`. The random lowercase directory name never changes when the display name changes. A new project initializes `README.md`, `AGENTS.md` or `CLAUDE.md`, Git and Beads when the prerequisites are available. A missing tool is a capability error, not a fake initialized repository; install it before retrying that operation.
3. The file tree includes hidden/configuration files. Create files/directories, upload, rename, move or delete. Markdown, images and PDFs have previews; text files can be edited. The file tree and linked-papers sections collapse independently. With no file selected, AI fills the remaining space; closing a preview returns that space to AI. Drag the splitters to resize panels.
4. External changes from an Agent or Git are polled. An unsaved editor buffer is not silently overwritten. On a save conflict, reload the latest version or save a separate copy after comparing; do not discard your draft just to clear an error.

File saves retain the displaced disk inode and submitted draft under `.easyread-revisions/`, also included in exports. This is not cross-process transactional CAS: the path can briefly be absent, but competing writes are not overwritten during publication. At 300 recovery entries or 200 MiB, saving pauses until you explicitly remove unneeded revisions through the file tree. Recovery files cannot themselves be overwritten; save a copy to an ordinary path. The filesystem must support hard links for complete-file publication. Managed Git operations likewise retain observed configuration conflicts; late writes to sanitizer-owned temporary handles are outside the guarantee. Stop external Git/Agent writers before managed Git operations.

Projects last updated over seven days ago are grouped in a collapsed “Projects from 7+ days ago” section; search still includes them. Research spaces use their own tabs in the multi-tab shell. The “+” action and the default post-authorization destination remain the library.

Autosave is on by default, with an explicit off preference. Type `@` in chat to select project files as removable context chips; the native CLI reads current file contents on demand. Current-project Markdown file links open in the preview, and tool activity is collapsed inside its own conversation turn, not a global activity list; intermediate tool retries do not permanently mark a successful turn as failed. Enter sends; Shift+Enter inserts a newline (Ctrl/⌘+Enter also sends).

### Papers and conversations

Search the EasyRead library and **link** papers. Links retain the original document ID; PDFs, notes and annotations are not copied into the project. Unlinking does not delete the library paper. A click opens a compact popover near the pointer with basic metadata, Read and Copy citation only; double-click opens the reader. Project MCP lets the Agent search/read library papers, notes and annotations, and link/unlink references. A project export does not include the linked library's original papers; preserve the library separately when moving installations.

Each project can have multiple Codex and Claude sessions. Choose a native engine for each session; switch, rename, cancel or remove sessions independently. Refreshing, selecting another file and switching to LaTeX do not intentionally create a fresh native conversation. Native session IDs and project-owned conversation state persist. Existing CLI login/configuration remains in the service account's CLI home. A native terminal is offered only when terminal support is available; a CLI session cannot run concurrently in chat and the terminal. Full Docker includes `ttyd`; source/desktop installations without it can still use chat.

### Git, Beads, import and export

Official `bd init` creates a local Git bootstrap commit (`bd init: initialize beads issue tracking`) for a new project’s initialization files. This upstream bootstrap behavior never pushes. Subsequent commits, remote changes, pull and push are explicit operations; force push is not provided. Configure your identity and remote credentials for the service account; never place tokens in a remote URL or export. Imported hooks/configuration are not trusted merely because they arrived in a ZIP.

Beads (`bd`) stores issue dependencies and Agent work records inside the project. The pinned official **1.3.4** release supports **embedded Dolt** by default; no shared SQL server or exposed database port is necessary. Full Docker also includes Dolt **2.7.5** for native maintenance/CLI use. Some upstream Go builds are server-only; prefer official embedded-capable release binaries. Do not confuse a JSONL export with the complete Beads database. Git push alone is not a guarantee that all native Beads data has been backed up.

Export/import the whole project through the project controls, including files, Git/Beads, metadata, full messages/events and project-owned durable native state. V2 session archives include subagents, tasks, file history, memory and supported Codex SQLite databases; import preserves native UUIDs while relocating workspace paths and database indexes. Host authentication/configuration, environment snapshots, runtime locks, diagnostic logs, regenerable global caches and external service state are excluded. Unknown native layouts/schemas fail explicitly until the adapter is updated, rather than silently dropping task state. Ordinary deletion is not secure erasure of Git/native database history. Imported projects receive independent identities and are not executed on import. Back up the complete library/data and the **separate CLI home securely** for disaster recovery; never publish that CLI-home backup. Stop active Agents and compilation before deleting a project or taking a filesystem backup.

## LaTeX editor

Open **LaTeX editor** in a project (`/workspaces?project=<id>&view=latex`). Its tree is scoped to `latex-workspace/`. First entry creates the directory and a minimal `main.tex` when it is empty. Existing sources are preserved. The project and LaTeX views share Agent sessions and references.

Choose the main `.tex` file relative to `latex-workspace/` and an engine: **pdfLaTeX**, **XeLaTeX** or **LuaLaTeX**. Main/engine settings persist in `latex-workspace/.easyread-latex.json`. LaTeX always autosaves and compilation waits for pending saves; there is no separate Save toolbar. The top settings button expands main-file, engine and font controls and closes on an outside click. Source text defaults to 12px, adjustable from 10–22px independently of reader typography and PDF zoom.

Local PDF.js workers render pages on demand (at most six canvases and two render jobs), with paging, zoom, fit-width, text selection, download and HTTP byte-range loading. A small button switches PDF/log views. With a SyncTeX-enabled build, use the source-locate button or Ctrl/⌘+Shift+Enter for forward search and double-click the PDF for inverse search. Recompile after source/PDF changes; previously built PDFs need one new compile to acquire mapping data.

Upload a LaTeX ZIP through the file tree: a single wrapper directory is removed and the main .tex file is detected. Same-name files require replacement confirmation; unrelated files stay intact and file/directory collisions are rejected. Imports never run scripts or compile automatically. Expanded archives are bounded to 128 MiB/10,000 entries.

Compilation runs asynchronously on the server: **three TeX passes**, with one **BibTeX or Biber** step after the first pass when the generated AUX/BCF requests a bibliography. The final two passes resolve citations and references. BibTeX supports local `.bib` files and local/installed `.bst` styles; Biber supports local BibTeX-format `.bib` resources. Missing bibliography tools or bibliography errors fail the build rather than displaying an incomplete PDF as success. Generated PDFs and logs/snapshot files stay in `latex-workspace/.easyread-build/<run-id>/`. A fresh build directory prevents an old PDF from being mistaken for a successful new build. Failed/cancelled builds never advertise the old PDF as their result. An interrupted service marks a running compilation as interrupted on reopening; it does not restart it. Only an explicit Compile action launches TeX.

Limits: two simultaneous builds per service; **120 seconds total** by default (`EASYREAD_TEX_TIMEOUT`, clamped to 5–600); 2 MiB captured compiler log; 128 MiB/10,000 source files; 64 MiB PDF/output budget. Linux additionally uses `prlimit` for per-process file-size, CPU and 2 GiB address-space limits when available. Symlink/special/hard-linked source files and traversal paths are rejected. The same time/log/output budget covers TeX and bibliography subprocesses; cancellation stops either. Only the current and two previous EasyRead-marked build directories are retained. Unmarked/user-created directories and older legacy snapshots are left untouched; remove those manually after all compiles stop if disk space is needed. Status/activity checks before first entry do not create `latex-workspace`; first entry still initializes an empty project without overwriting existing sources.

The compiler uses a restricted environment, paranoid TeX input/output settings, no shell escape, and LuaTeX `--nosocket`. `luaotfload/fontspec` reject `--safer`, so it is not used: **LuaLaTeX can execute embedded Lua with the service account’s file access**. Compile only inspected, trusted documents; disabling shell escape does not prohibit Lua filesystem access. Project `.latexmkrc` files are **not executed**. Packages that require shell execution (for example `minted`/externalization) are intentionally unavailable in this compiler. Bibliography controls are validated before execution: Biber runs with `--noconf`; remote resources, globs, external XML entities, foreign input drivers and filesystem-affecting BCF options are rejected. Keep bibliography data in this project's LaTeX directory. This fixed TeX → bibliography → TeX → TeX pipeline does not execute a complete project-controlled `latexmk` build graph. On source/desktop installations, install missing TeX packages beforehand; no unattended package installation is requested during a compile. These mitigations do not turn a full-permission, same-user Agent or a vulnerable TeX binary into an OS sandbox. Do not compile untrusted public uploads.

## Installation and diagnostics

| Distribution | Included | Additional setup |
| --- | --- | --- |
| Full Docker | Git, official bd + Dolt, TeX Live with all three engines/latexmk/Biber and common LaTeX/CJK packages, MCP Python runtime, Codex/Claude, ttyd | Sign into CLI; configure Git identity/remote; set access protection |
| Lite Docker | EasyRead/Python and MCP | External native tools are intentionally absent. Use full for the complete research environment; file editing/library access remains available with capability diagnostics |
| Desktop | Frozen Python service including MCP and the workspace adapter | Install Git, bd, TeX distribution and chosen Agent CLI on the host; restart EasyRead after changing PATH |
| Source | Python dependencies including MCP from `pip install -e .` | Install the same native tools in PATH; Node 22 is needed to rebuild editor/desktop assets |

From a source checkout (the helper is also copied beside the packaged backend):

```bash
# Read-only diagnostics: no subprocesses, downloads, login or repo initialization.
python scripts/install_workspace_deps.py --check
# Explicit install: official bd/Dolt binaries only, with SHA-256 verification.
python scripts/install_workspace_deps.py --install
# Add ~/.local/bin to PATH, then restart EasyRead.
```

The installer supports Linux/macOS amd64/arm64 and Windows amd64 for the default Dolt release. Windows arm64 users should use an upstream-supported Dolt package/emulation setup. `--prefix` selects a different installation prefix. It does not install system packages, edit shell profiles, initialize a development repository or alter authentication. Downloads respect `HTTPS_PROXY` and retry boundedly. Package-manager commands are printed for review; TeX can require several GB.

**Debian/Ubuntu:**

```bash
sudo apt-get update
sudo apt-get install -y git texlive-latex-base texlive-latex-recommended \
  texlive-latex-extra texlive-fonts-recommended texlive-xetex texlive-luatex \
  texlive-lang-chinese texlive-bibtex-extra latexmk biber
```

**macOS:** `brew install git beads dolt` and `brew install --cask mactex-no-gui`. Make `/Library/TeX/texbin` visible to EasyRead. **Windows:** `winget install Git.Git` and `winget install MiKTeX.MiKTeX`; use MiKTeX Console to install/update required packages before compiling. Install bd/Dolt with the helper or [upstream packages](https://github.com/gastownhall/beads/blob/v1.3.4/docs/getting-started/installation.md). Use the [Codex](https://developers.openai.com/codex/cli/) or [Claude Code](https://code.claude.com/docs/en/setup) installation/login instructions. Restart the desktop app/service after installation.

For nonstandard TeX locations, server-owned environment variables `EASYREAD_TEX_PDFLATEX`, `EASYREAD_TEX_XELATEX` and `EASYREAD_TEX_LUALATEX` accept executable paths, not shell commands. `EASYREAD_TEX_BIBTEX` and `EASYREAD_TEX_BIBER` similarly override the bibliography executable paths. The UI still accepts only the three TeX engine names. Missing executables are reported; a green path probe alone does not prove the distribution has the packages your paper needs.

## Deployment, upgrades and verification

Use the [full Docker guide](docker.en.md). `.env.example` and Compose expose `BEADS_VERSION=1.3.4`, `DOLT_VERSION=2.7.5`, `DOLT_SHA256` (required for another Dolt release) and `EASYREAD_TEXLIVE_PACKAGES` (APT package list; `package=version` pins work if the repository retains them). Existing Codex/Claude version controls remain separate. Full images install their native platform binaries through official npm packages with npm integrity verification; `CLAUDE_CODE_VERSION` accepts stable/latest or a pinned version. `easyread-cli update` supports both npm and existing standalone installations without changing authentication directories. The root Dockerfile also accepts `PYTHON_IMAGE`/`NODE_IMAGE` for explicit base-image pins. The native installer verifies the bd release checksum manifest and pinned/per-override Dolt checksums and preserves upstream license notices. No app version bump is needed to test an adapter against another dependency release.

Changing native prerequisites requires rebuilding the **root full Dockerfile**, not the code-only `docker/Dockerfile.app` path. That path checks for the new runtime and fails rather than silently reusing an old CLI-only image. Data and CLI-home volumes persist; dependency rebuilds do not replace user login files. Never use `docker compose down -v` as an upgrade command.

Before a Beads upgrade, read the [upstream upgrade guide](https://github.com/gastownhall/beads/blob/v1.3.4/docs/getting-started/installation.md#updating-bd), make a full backup and test a disposable copy. Schema migrations may prevent downgrade. EasyRead does not automatically run upstream migration/hook installers on imported projects. TeX upgrades should recheck the same paper under all chosen engines. UI editor dependencies are built separately; no Aldine/TeXlyre service is required.

```bash
python -m unittest discover -s tests -p test_workspace_latex.py -v
# Inside a freshly built full image, using temporary projects and HOME only:
python /usr/local/bin/easyread-check-workspace-runtime
python /usr/local/bin/easyread-check-workspace-runtime --adapter
```

The full Docker build runs those real native/adapter smoke checks, including `bd init/create/list`, all three TeX engines, PDFs and diagnostic lines, plus actual bibliography text extracted from BibTeX/Biber PDFs through the async adapter. Unit tests labelled **MOCK** exercise subprocess control and safety logic but are not evidence of a real TeX installation. Desktop packages still need native-platform installation/PATH and compilation validation; a Linux image does not establish Windows/macOS acceptance.

## Upstream references

Design references: [Aldine](https://github.com/trahloff/Aldine) (persistent project build outputs and line diagnostics) and [TeXlyre](https://github.com/TeXlyre/texlyre) (source/PDF panels and editor feedback). Both are **AGPL-3.0**; no source/assets from either are copied or bundled here. EasyRead's server compiler is an independent adapter, not either project's compiler service or WASM engine. [Beads](https://github.com/gastownhall/beads) is MIT; [Dolt](https://github.com/dolthub/dolt) is Apache-2.0; TeX Live packages retain their individual licenses. See also [TeX Live documentation](https://www.tug.org/texlive/doc.html) and [LuaTeX options](https://manpages.debian.org/bookworm/texlive-binaries/luatex.1.en.html).

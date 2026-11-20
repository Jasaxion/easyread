#!/usr/bin/env python3
"""Real, credential-free runtime smoke check in disposable directories.

Default checks native packages during the dependency image build. --adapter
checks EasyRead's actual async compiler after the application is installed.
Never points bd, git, TeX, or Agent CLIs at the user's library or HOME.
"""
import argparse
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def document(engine):
    fonts = "\\usepackage{fontspec}\n" if engine in ("xelatex", "lualatex") else ""
    return "\\documentclass{article}\n" + fonts + "\\begin{document}\nReal EasyRead runtime check.\\label{test}\nSee \\ref{test}.\n\\end{document}\n"


def bibliography_document(engine, tool):
    source = document(engine)
    if tool == "biber":
        source = source.replace("\\begin{document}", "\\usepackage[backend=biber]{biblatex}\n\\addbibresource{references.bib}\n\\begin{document}")
        bibliography = "\\printbibliography"
    else:
        bibliography = "\\bibliographystyle{plain}\n\\bibliography{references}"
    return source.replace("\\end{document}", "Cited result \\cite{runtime}.\n" + bibliography + "\n\\end{document}")


def native():
    importlib.import_module("mcp.server.fastmcp")
    for name in ("git", "bd", "dolt", "pdflatex", "xelatex", "lualatex", "latexmk", "bibtex", "biber", "synctex"):
        if not shutil.which(name):
            raise RuntimeError(f"Missing full-image prerequisite: {name}")
    with tempfile.TemporaryDirectory(prefix="easyread-runtime-check-") as temporary:
        root = Path(temporary)
        home = root / "home"
        home.mkdir()
        env = {key: value for key, value in os.environ.items()
               if key in ("PATH", "LANG", "LC_ALL", "SystemRoot", "WINDIR")}
        env.update(HOME=str(home), USERPROFILE=str(home), GIT_CONFIG_NOSYSTEM="1", CI="true",
                   DOLT_CLI_NO_METRICS="1", BEADS_NO_DAEMON="1")
        repo = root / "project"
        repo.mkdir()
        for command in (["git", "init", "--quiet"], ["git", "config", "user.name", "Runtime check"],
                        ["git", "config", "user.email", "check@localhost"],
                        ["bd", "init", "--quiet", "--skip-hooks", "--skip-agents", "--prefix", "check"],
                        ["bd", "create", "Runtime smoke", "--type", "task", "--json"], ["bd", "list", "--json"],
                        ["dolt", "version"]):
            subprocess.run(command, cwd=repo, env=env, stdin=subprocess.DEVNULL, check=True, timeout=120)
        for engine in ("pdflatex", "xelatex", "lualatex"):
            work = root / engine
            work.mkdir()
            (work / "main.tex").write_text(document(engine))
            tex_env = dict(env, openin_any="p", openout_any="p", shell_escape="f")
            command = [engine, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error"]
            if engine == "lualatex":
                command += ["--nosocket"]
            result = subprocess.run(command + ["main.tex"], cwd=work, env=tex_env, stdin=subprocess.DEVNULL,
                                    capture_output=True, timeout=120)
            if result.returncode or not (work / "main.pdf").exists():
                raise RuntimeError(f"{engine}: {result.stdout.decode(errors='replace')[-6000:]}")
            assert (work / "main.pdf").read_bytes().startswith(b"%PDF-")
            print(f"PASS: real {engine} PDF", flush=True)
    print("PASS: real Git + embedded Beads create/list + Dolt + MCP", flush=True)


def adapter():
    from easyread.workspaces import latex
    with tempfile.TemporaryDirectory(prefix="easyread-tex-adapter-") as temporary:
        workspace = Path(temporary)
        for engine in latex.ENGINES:
            latex.initialize(workspace)
            main = workspace / "latex-workspace/main.tex"
            main.write_text(document(engine))
            latex.start(workspace, {"engine": engine})
            deadline = time.monotonic() + 130
            while latex.status(workspace)["compilation"]["state"] == "running" and time.monotonic() < deadline:
                time.sleep(.1)
            result = latex.status(workspace)["compilation"]
            latex.close(workspace)
            if result["state"] != "done":
                raise RuntimeError(f"{engine} adapter: {result}")
            assert (workspace / result["pdf"]).read_bytes().startswith(b"%PDF-")
            for tool in ("bibtex", "biber"):
                (main.parent / "references.bib").write_text('@book{runtime,author={Research, Ada},title={Bibliography smoke result},year={2026},publisher={EasyRead}}')
                main.write_text(bibliography_document(engine, tool))
                latex.start(workspace)
                deadline = time.monotonic() + 130
                while latex.busy(workspace) and time.monotonic() < deadline:
                    time.sleep(.1)
                result = latex.status(workspace)["compilation"]
                latex.close(workspace)
                if result["state"] != "done" or result.get("bibliography") != tool:
                    raise RuntimeError(f"{engine}/{tool} adapter: {result}")
                from pypdf import PdfReader
                text = " ".join(" ".join(page.extract_text() or "" for page in PdfReader(workspace / result["pdf"]).pages).split())
                assert "Bibliography smoke result" in text, (engine, tool, text)
                print(f"PASS: real {engine}/{tool} bibliography text in PDF", flush=True)
            # A previous successful PDF must not mask the next compilation error.
            main.write_text("\\documentclass{article}\n\\begin{document}\n\\badcommand\n\\end{document}\n")
            latex.start(workspace)
            deadline = time.monotonic() + 130
            while latex.status(workspace)["compilation"]["state"] == "running" and time.monotonic() < deadline:
                time.sleep(.1)
            result = latex.status(workspace)["compilation"]
            latex.close(workspace)
            assert result["state"] == "error" and not result.get("pdf"), result
            assert result["errors"][0]["line"] == 3, result
            print(f"PASS: real {engine} async adapter / PDF / error line / no stale PDF", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", action="store_true")
    if parser.parse_args().adapter:
        adapter()
    else:
        native()

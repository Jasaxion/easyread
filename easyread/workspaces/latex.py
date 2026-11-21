"""Project-local TeX compilation; no store or HTTP dependencies.

Only explicit start() launches a compiler. Builds use a symlink-free snapshot,
not latexmk/project startup scripts. This is defense in depth for a trusted
single-user workspace, not an OS sandbox for hostile TeX or same-user agents.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET

ENGINES = ("pdflatex", "xelatex", "lualatex")
STATE_FILE = ".easyread-latex.json"
BUILD_DIR = ".easyread-build"
MAX_LOG = 2 * 1024 * 1024
MAX_FILES = 10000
MAX_SOURCE = 128 * 1024 * 1024
MAX_OUTPUT = 64 * 1024 * 1024
_LOCK = threading.RLock()
_JOBS: dict[str, "_Job"] = {}
_SLOTS = threading.BoundedSemaphore(2)
_TEMPLATE = "\\documentclass{article}\n\\begin{document}\nHello, research workspace!\n\\end{document}\n"


def _safe(path: Path) -> Path:
    """Reject links and non-regular objects, including hard-linked files."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise ValueError(f"Unsafe LaTeX path: {path.name}")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise ValueError(f"Hard-linked LaTeX file: {path.name}")
    return path


def _root(workspace: Path, *, create: bool = True) -> Path:
    workspace = Path(workspace).absolute()
    _safe(workspace)
    if not workspace.is_dir():
        raise ValueError("Workspace does not exist")
    root = _safe(workspace / "latex-workspace")
    if create:
        root.mkdir(exist_ok=True)
    return root


def _main(root: Path, value: str, *, exists: bool = True) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("Main must be a relative .tex file")
    # TeX parses its last argument as input, so also forbid TeX metacharacters.
    if any(c in value for c in "\\:{}%$&#^~\x00\"'") or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid main file path")
    parts = value.split("/")
    if any(p in ("", ".", "..") or p.startswith("-") for p in parts):
        raise ValueError("Main must stay inside latex-workspace")
    if parts[0] == BUILD_DIR or not value.lower().endswith(".tex"):
        raise ValueError("Main must be a source .tex file")
    path = root
    for part in parts:
        path = _safe(path / part)
    if exists and not path.is_file():
        raise ValueError("Main .tex file does not exist")
    return PurePosixPath(value).as_posix()


def _engine(value: str) -> str:
    if value not in ENGINES:
        raise ValueError("Supported engines: pdflatex, xelatex, lualatex")
    return value


def _executable(engine: str) -> str | None:
    # Server-owned override only; never accept executable paths from HTTP/state.
    return shutil.which(os.environ.get(f"EASYREAD_TEX_{engine.upper()}", engine))


def capabilities() -> list[dict]:
    return [{"id": engine, "installed": bool(_executable(engine)),
             "install": "Install TeX Live or MiKTeX; see docs/research-workspaces.en.md"}
            for engine in ENGINES]


def _idle() -> dict:
    return {"state": "idle", "log": "", "errors": []}


def _save(root: Path, state: dict) -> None:
    _safe(root)
    _safe(root / STATE_FILE)
    fd, temporary = tempfile.mkstemp(prefix=".latex-state-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
        os.replace(temporary, root / STATE_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load(root: Path, *, create: bool = True) -> dict:
    path = _safe(root / STATE_FILE)
    if not path.exists():
        state = {"main": "main.tex", "engine": "pdflatex", "compilation": _idle()}
        if create:
            _save(root, state)
        return state
    if path.stat().st_size > MAX_LOG * 2:
        raise ValueError("LaTeX settings file is too large")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        _main(root, state["main"], exists=False)
        _engine(state["engine"])
        compilation = state["compilation"]
        if not isinstance(compilation, dict) or compilation.get("state") not in ("idle", "running", "done", "error"):
            raise ValueError("Invalid compilation state")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid LaTeX settings file") from exc
    if compilation["state"] == "running" and str(root) not in _JOBS:
        compilation.update(state="error", code="interrupted", errors=[{
            "file": state["main"], "line": None, "message": "Compilation interrupted by service restart"}])
        compilation.pop("pdf", None)
        _save(root, state)
    # Never advertise an imported/stale path outside this project's build tree.
    pdf = compilation.get("pdf")
    if pdf:
        parts = pdf.split("/") if isinstance(pdf, str) else []
        valid = len(parts) >= 4 and parts[:2] == ["latex-workspace", BUILD_DIR]
        valid = valid and all(p not in ("", ".", "..") and "\\" not in p and ":" not in p for p in parts)
        path = root
        if valid:
            for part in parts[1:]:
                path = _safe(path / part)
            valid = path.is_file() and path.suffix == ".pdf"
        if not valid or compilation["state"] != "done":
            compilation.pop("pdf", None)
    if compilation["state"] == "done" and not compilation.get("pdf"):
        compilation.update(state="error", errors=[{
            "file": state["main"], "line": None, "message": "Compiled PDF is missing or was moved; compile again"}])
    return state


def busy(workspace: Path | None = None) -> bool:
    """In-process activity probe; never initializes a project or TeX directory."""
    with _LOCK:
        return bool(_JOBS) if workspace is None else str(Path(workspace).absolute() / "latex-workspace") in _JOBS


def status(workspace: Path) -> dict:
    with _LOCK:
        state = _load(_root(workspace, create=False), create=False)
        return {**state, "engines": capabilities()}


def initialize(workspace: Path, *, if_version=None) -> dict:
    with _LOCK:
        root = _root(workspace)
        state = _load(root)
        if not state.get("initialized"):
            # A pre-entry status record from older versions is not an entry.
            # Never overwrite existing sources, nor recreate a deleted starter
            # after a genuine initialization.
            if not any(p.name != STATE_FILE for p in root.iterdir()):
                (root / "main.tex").write_text(_TEMPLATE, encoding="utf-8")
            state["initialized"] = True
            _save(root, state)
        result = status(workspace)
        # Include validated PDF state and live engine availability, not just the
        # persisted settings/log: either can change without a settings write.
        version = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if if_version == version:
            return {"unchanged": True, "version": version}
        return {**result, "version": version}


def settings(workspace: Path, body: dict) -> dict:
    with _LOCK:
        root = _root(workspace)
        state = _load(root)
        if str(root) in _JOBS:
            raise ValueError("Compilation is running; cancel or wait before changing settings")
        main = _main(root, body.get("main", state["main"]))
        engine = _engine(body.get("engine", state["engine"]))
        if (main, engine) != (state["main"], state["engine"]):
            state["compilation"] = _idle()
        state.update(main=main, engine=engine)
        _save(root, state)
        return {**state, "engines": capabilities()}


def _snapshot(root: Path, build: Path, cancelled: threading.Event, deadline: float) -> tuple[int, dict[str, str]]:
    count = total = 0
    sources = {}
    def visit(source: Path, target: Path) -> None:
        nonlocal count, total
        for child in source.iterdir():
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise RuntimeError("Compilation cancelled or timed out while preparing sources")
            if source == root and child.name in (BUILD_DIR, STATE_FILE):
                continue
            _safe(child)
            count += 1
            if count > MAX_FILES:
                raise ValueError("Too many LaTeX source files")
            dest = target / child.name
            if child.is_dir():
                dest.mkdir()
                visit(child, dest)
            else:
                # O_NOFOLLOW closes the common final-component replacement race.
                fd = os.open(child, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as src:
                    info = os.fstat(src.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("Unsafe LaTeX source")
                    total += info.st_size
                    if total > MAX_SOURCE:
                        raise ValueError("LaTeX source exceeds 128 MiB")
                    digest = hashlib.sha256()
                    with dest.open("xb") as out:
                        remaining = info.st_size
                        while remaining:
                            if cancelled.is_set() or time.monotonic() >= deadline:
                                raise RuntimeError("Compilation cancelled or timed out while preparing sources")
                            chunk = src.read(min(65536, remaining))
                            if not chunk:
                                raise ValueError("LaTeX source changed while preparing snapshot")
                            out.write(chunk)
                            digest.update(chunk)
                            remaining -= len(chunk)
                    sources[child.relative_to(root).as_posix()] = digest.hexdigest()
    visit(root, build)
    return total, sources


def _prepare_build(root: Path) -> Path:
    """Retain current + two previous marked builds; leave user directories alone."""
    build_root = _safe(root / BUILD_DIR)
    build_root.mkdir(exist_ok=True)
    build = build_root / uuid.uuid4().hex
    build.mkdir(mode=0o700)
    marker = build_root / (build.name + ".json")
    with marker.open("x", encoding="utf-8") as stream:
        json.dump({"kind": "easyread-latex-build", "id": build.name, "created": time.time()}, stream)
    previous = []
    for entry in build_root.iterdir():
        if not re.fullmatch(r"[a-f0-9]{32}", entry.name) or entry == build:
            continue
        record = build_root / (entry.name + ".json")
        try:
            _safe(entry)
            _safe(record)
            if not entry.is_dir() or not record.is_file() or record.stat().st_size > 1024:
                continue
            data = json.loads(record.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("kind") != "easyread-latex-build" or data.get("id") != entry.name or type(data.get("created")) not in (int, float):
                continue
            previous.append((data["created"], entry, record))
        except (ValueError, OSError):
            continue
    for _, entry, record in sorted(previous, key=lambda item: item[0], reverse=True)[2:]:
        # rmtree does not follow child symlinks; _safe rejects a linked root.
        _safe(entry)
        shutil.rmtree(entry)
        record.unlink()
    return build


def _environment(build: Path) -> dict:
    # Do not inherit CLI secrets, TEXINPUTS, TEXMFCNF or loader overrides.
    keep = ("PATH", "SystemRoot", "WINDIR", "LANG", "LC_ALL", "FONTCONFIG_PATH")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env.update(HOME=str(build), USERPROFILE=str(build), TMPDIR=str(build), TEMP=str(build), TMP=str(build),
               openin_any="p", openout_any="p", shell_escape="f", TEXMFOUTPUT=str(build),
               TEXMFHOME=str(build / ".texmf"), TEXMFCONFIG=str(build / ".texmf-config"),
               TEXMFVAR=str(build / ".texmf-var"), MIKTEX_ENABLE_INSTALLER="0")
    return env


def _bibliography_path(root: Path, value: str, suffix: str) -> Path:
    value = value.strip()
    if not value or len(value) > 512 or any(c in value for c in "\\:{}%|<>~!?\"'\x00") or any(ord(c) < 32 for c in value):
        raise ValueError("Unsafe bibliography path")
    parts = value.split("/")
    if any(p in ("", ".", "..") or p.startswith("-") for p in parts):
        raise ValueError("Bibliography paths must stay inside the build snapshot")
    path = root
    for part in parts:
        path = _safe(path / part)
    if path.suffix.lower() != suffix:
        path = _safe(Path(str(path) + suffix))
    return path


def _control_text(path: Path) -> str:
    _safe(path)
    if path.stat().st_size > MAX_LOG:
        raise ValueError("Bibliography control file exceeds 2 MiB")
    return path.read_text(encoding="utf-8", errors="strict")


def _bibliography_tool(build: Path, output: Path) -> str | None:
    """Validate generated controls before allowing a fixed BibTeX/Biber command.

    Biber's XML can otherwise select remote sources, arbitrary Perl input
    drivers or filesystem-affecting options, even with --noconf.
    """
    bcf = _safe(output / "easyread.bcf")
    if bcf.exists():
        text = _control_text(bcf)
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
            raise ValueError("Bibliography XML declarations/entities are not allowed")
        try:
            document = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ValueError("Invalid Biber control XML") from exc
        namespace = "{https://sourceforge.net/projects/biblatex}"
        # Current biblatex uses the first URI; accept the older official URI too.
        namespaces = (namespace, "{http://biblatex-biber.sourceforge.net/bcf}")
        if document.tag not in tuple(n + "controlfile" for n in namespaces):
            raise ValueError("Unsupported Biber control namespace")
        namespace = document.tag.removesuffix("controlfile")
        safe_options = {"input_encoding", "output_encoding", "debug", "mincrossrefs", "minxrefs", "sortcase", "sortupper"}
        sources = []
        for element in document.iter():
            if not isinstance(element.tag, str) or not element.tag.startswith(namespace):
                raise ValueError("Foreign bibliography XML elements are not allowed")
            if element.tag == namespace + "options" and element.get("component") == "biber":
                for option in element:
                    key = option.findtext(namespace + "key")
                    if key not in safe_options:
                        raise ValueError(f"Unsupported Biber control option: {key}")
            if element.tag == namespace + "datasource":
                if element.get("type") != "file" or element.get("datatype") != "bibtex" or element.get("glob", "false").lower() not in ("false", "0"):
                    raise ValueError("Biber supports local BibTeX .bib sources only; remote sources/globs are disabled")
                path = _bibliography_path(build, element.text or "", ".bib")
                if not path.is_file():
                    raise ValueError(f"Bibliography source does not exist: {element.text}")
                sources.append(path)
        return "biber" if sources else None
    aux = output / "easyread.aux"
    if not aux.exists():
        return None
    visited = set()
    has_data = False
    def validate(path: Path) -> None:
        nonlocal has_data
        if path in visited:
            return
        visited.add(path)
        if len(visited) > 1000:
            raise ValueError("Too many bibliography auxiliary files")
        text = _control_text(path)
        for marker in re.finditer(r"\\(?:bibdata|bibstyle|@input)\b", text):
            match = re.match(r"\\(bibdata|bibstyle|@input)\s*\{([^{}\r\n]*)\}", text[marker.start():])
            if not match:
                raise ValueError("Malformed bibliography auxiliary directive")
            directive, value = match.groups()
            if directive == "@input":
                validate(_bibliography_path(output, value, ".aux"))
            elif directive == "bibdata":
                has_data = True
                for name in value.split(","):
                    source = _bibliography_path(build, name, ".bib")
                    generated = _bibliography_path(output, name, ".bib")
                    if not source.is_file() and not generated.is_file():
                        raise ValueError(f"Bibliography source does not exist: {name}")
            else:
                style = _bibliography_path(build, value, ".bst")
                if not style.is_file() and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", value):
                    raise ValueError("Bibliography style must be local or an installed style name")
    validate(aux)
    return "bibtex" if has_data else None


def _errors(log: str, main: str) -> list[dict]:
    errors = []
    pending = None
    for line in log.splitlines():
        match = re.match(r"^(.+?\.(?:tex|sty|cls|bib)):(\d+):\s*(.*)", line)
        if match:
            file = match[1].removeprefix("./")
            errors.append({"file": file, "line": int(match[2]), "message": match[3]})
        elif match := re.match(r"^(.*?)---line\s+(\d+)\s+of file\s+(.+\.(?:bib|bst|aux))\s*$", line):
            errors.append({"file": match[3], "line": int(match[2]), "message": match[1].strip()})
        elif line.startswith("!"):
            pending = line.lstrip("! ")
        elif pending and (match := re.match(r"l\.(\d+)\s", line)):
            errors.append({"file": main, "line": int(match[1]), "message": pending})
            pending = None
    if pending:
        errors.append({"file": main, "line": None, "message": pending})
    # Repeated passes commonly repeat the same diagnostic.
    return [dict(items) for items in dict.fromkeys(tuple(e.items()) for e in errors)][:200]


class _Job:
    def __init__(self, root: Path, state: dict, executable: str):
        self.root, self.state, self.executable = root, state, executable
        self.cancelled = threading.Event()
        self.process: subprocess.Popen | None = None
        self.thread = threading.Thread(target=self.run, name="workspace-tex", daemon=True)
        self.log = bytearray()
        self.source_size = 0
        self.log_lock = threading.Lock()
        self.overflow = threading.Event()

    def stop(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    # XeTeX can launch xdvipdfmx; kill the Windows process tree,
                    # not just its parent, before allowing project deletion.
                    taskkill = shutil.which("taskkill")
                    if taskkill:
                        subprocess.run([taskkill, "/PID", str(process.pid), "/T", "/F"],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, timeout=5, check=False)
                    if process.poll() is None:
                        process.kill()
            except ProcessLookupError:
                pass

    def check_output(self, build: Path) -> None:
        count = total = 0
        for directory, _, files in os.walk(build, followlinks=False):
            for name in files:
                path = _safe(Path(directory) / name)
                count += 1
                total += path.stat().st_size
                if count > MAX_FILES * 2 or total > self.source_size + MAX_OUTPUT:
                    raise RuntimeError("Compiler workspace exceeded output limits")

    def read_output(self, stream) -> None:
        try:
            while chunk := stream.read(8192):
                with self.log_lock:
                    room = MAX_LOG - len(self.log)
                    self.log.extend(chunk[:room])
                    if len(chunk) > room:
                        self.overflow.set()
        finally:
            stream.close()

    def text(self) -> str:
        with self.log_lock:
            return self.log.decode("utf-8", errors="replace")

    def publish(self) -> None:
        with _LOCK:
            self.state["compilation"]["log"] = self.text()
            _save(self.root, self.state)

    def execute(self, command: list[str], label: str, build: Path,
                deadline: float, timeout: int, *, cwd: Path, env: dict) -> None:
        if self.cancelled.is_set():
            raise RuntimeError("Compilation cancelled")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Compilation exceeded {timeout} seconds")
        # Never use preexec_fn in the threaded server: inherited locks may deadlock.
        if os.name == "posix" and shutil.which("prlimit"):
            command = [shutil.which("prlimit"), f"--fsize={MAX_OUTPUT}", "--as=2147483648",
                       f"--cpu={timeout}", "--", *command]
        with self.log_lock:
            self.log.extend(f"\n--- {label} ---\n".encode())
        self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        start_new_session=os.name == "posix",
                                        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0)
        reader = threading.Thread(target=self.read_output, args=(self.process.stdout,), daemon=True)
        reader.start()
        try:
            while self.process.poll() is None:
                if self.cancelled.is_set():
                    raise RuntimeError("Compilation cancelled")
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Compilation exceeded {timeout} seconds")
                if self.overflow.is_set():
                    raise RuntimeError("Compiler log exceeded 2 MiB")
                self.check_output(build)
                self.publish()
                time.sleep(0.1)
        finally:
            self.stop()
            self.process.wait(timeout=5)
            reader.join(timeout=5)
        if self.cancelled.is_set():
            raise RuntimeError("Compilation cancelled")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Compilation exceeded {timeout} seconds")
        if self.overflow.is_set():
            raise RuntimeError("Compiler log exceeded 2 MiB")
        self.check_output(build)
        if self.process.returncode:
            raise RuntimeError(f"{label} exited with code {self.process.returncode}")

    def run(self) -> None:
        compilation = self.state["compilation"]
        main = self.state["main"]
        try:
            timeout = min(600, max(5, int(os.environ.get("EASYREAD_TEX_TIMEOUT", "120"))))
            deadline = time.monotonic() + timeout
            build = _prepare_build(self.root)
            self.source_size, sources = _snapshot(self.root, build, self.cancelled, deadline)
            output = Path(tempfile.mkdtemp(prefix="output-", dir=build))
            # TeX's \\include writes nested .aux files below the output directory.
            for directory, names, _ in os.walk(build):
                if Path(directory) == build:
                    names.remove(output.name)
                (output / Path(directory).relative_to(build)).mkdir(exist_ok=True)
            engine = self.state["engine"]
            command = [self.executable, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
                       "-file-line-error", "-no-parse-first-line", "-synctex=1", f"-output-directory={output.name}", "-jobname=easyread"]
            if engine == "lualatex":
                # luaotfload/fontspec explicitly reject --safer. This trusted
                # code workspace is not an OS sandbox; embedded Lua can do I/O.
                command.append("--nosocket")
            command.append("./" + main)
            for number in range(1, 4):
                self.execute(command, f"{engine}: pass {number}/3", build, deadline, timeout,
                             cwd=build, env=_environment(build))
                if number == 1:
                    tool = _bibliography_tool(build, output)
                    if tool:
                        executable = _executable(tool)
                        if not executable:
                            compilation["code"] = "missing_bibliography_tool"
                            raise RuntimeError(f"{tool} not installed. Install {tool} with your TeX distribution.")
                        compilation["bibliography"] = tool
                        env = _environment(build)
                        if tool == "bibtex":
                            # Controls were validated above; default search tail
                            # permits installed .bst styles, not external .bib paths.
                            env.update(BIBINPUTS=f"{build}//{os.pathsep}", BSTINPUTS=f"{build}//{os.pathsep}")
                            arguments = [executable, "easyread"]
                            cwd = output
                        else:
                            # Biber 2.x otherwise derives an absolute output/log
                            # basename from the absolute control-file argument.
                            arguments = [executable, "--noconf", f"--input-directory={build}",
                                         f"--output-directory={output}", "--output-file=easyread.bbl",
                                         "--logfile=easyread.blg", str(output / "easyread.bcf")]
                            cwd = build
                        self.execute(arguments, tool, build, deadline, timeout, cwd=cwd, env=env)
                        bbl = _safe(output / "easyread.bbl")
                        if not bbl.is_file() or not bbl.stat().st_size:
                            raise RuntimeError(f"{tool} did not produce a bibliography")
            if self.cancelled.is_set():
                raise RuntimeError("Compilation cancelled")
            pdf = _safe(output / "easyread.pdf")
            if not pdf.is_file() or pdf.stat().st_size > MAX_OUTPUT:
                raise RuntimeError("Compiler did not produce a bounded PDF")
            with pdf.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise RuntimeError("Compiler did not produce a valid PDF header")
            from . import latex_sync
            compilation.update(synctex=latex_sync.record(build, pdf, sources))
            compilation.update(state="done", pdf="latex-workspace/" + pdf.relative_to(self.root).as_posix(), errors=[])
        except Exception as exc:
            compilation.update(state="error", errors=_errors(self.text(), main) or [
                {"file": main, "line": None, "message": str(exc)}])
            compilation.pop("pdf", None)
            if self.cancelled.is_set():
                compilation["code"] = "cancelled"
            with self.log_lock:
                message = ("\n" + str(exc) + "\n").encode()
                self.log = self.log[:MAX_LOG - len(message)] + message
        finally:
            self.stop()
            compilation["finished"] = time.time()
            with _LOCK:
                try:
                    self.publish()
                finally:
                    _JOBS.pop(str(self.root), None)
                    _SLOTS.release()


def start(workspace: Path, body: dict | None = None) -> dict:
    with _LOCK:
        initialize(workspace)
        root = _root(workspace)
        if str(root) in _JOBS:
            return status(workspace)
        state = settings(workspace, body or {})
        state.pop("engines", None)
        executable = _executable(state["engine"])
        if not executable:
            state["compilation"] = {"state": "error", "code": "missing_engine", "log": "", "errors": [{
                "file": state["main"], "line": None,
                "message": f"{state['engine']} not installed. Install TeX Live or MiKTeX; see the research workspace guide."}]}
            _save(root, state)
            return {**state, "engines": capabilities()}
        if not _SLOTS.acquire(blocking=False):
            raise ValueError("Two LaTeX compilations are already running; retry later")
        state["compilation"] = {"state": "running", "log": "", "errors": [], "started": time.time()}
        try:
            _save(root, state)
            job = _Job(root, state, executable)
            _JOBS[str(root)] = job
            job.thread.start()
        except Exception:
            _JOBS.pop(str(root), None)
            _SLOTS.release()
            raise
        return {**copy.deepcopy(state), "engines": capabilities()}


def cancel(workspace: Path) -> dict:
    with _LOCK:
        root = _root(workspace, create=False)
        job = _JOBS.get(str(root))
        if job:
            job.cancelled.set()
            job.stop()
    if job:
        job.thread.join(timeout=10)
        if job.thread.is_alive():
            raise TimeoutError("Compilation is still stopping; do not delete the workspace yet")
    return status(workspace)


def close(workspace: Path | None = None) -> None:
    """Call before project deletion / server shutdown; no restart-on-open."""
    if workspace is not None:
        cancel(workspace)
        return
    with _LOCK:
        jobs = list(_JOBS.values())
        for job in jobs:
            job.cancelled.set()
            job.stop()
    for job in jobs:
        job.thread.join(timeout=10)
        if job.thread.is_alive():
            raise TimeoutError("Compilation is still stopping")

"""Version-bound SyncTeX queries against retained compiler snapshots.

SyncTeX is invoked without viewer/editor commands in a private query directory.
Its boxes use PDF big points: h is the left edge, v the bottom, H the height.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading

from . import WorkspaceError, files, latex

MANIFEST = ".easyread-sync.json"
MAX_MANIFEST = 4 * 1024 * 1024
MAX_QUERY_OUTPUT = 1024 * 1024
_QUERY_SLOTS = threading.BoundedSemaphore(2)


def digest(path: Path, limit: int) -> str:
    result, size = hashlib.sha256(), 0
    with files.open_regular(path) as stream:
        while chunk := stream.read(65536):
            size += len(chunk)
            if size > limit:
                raise WorkspaceError("SyncTeX file exceeds size limit", 413)
            result.update(chunk)
    return result.hexdigest()


def record(build: Path, pdf: Path, sources: dict[str, str]) -> bool:
    """Called only after a successful compile; absent SyncTeX is not a PDF error."""
    sync = pdf.with_suffix(".synctex.gz")
    if not sync.exists():
        return False
    from pypdf import PdfReader
    with files.open_regular(pdf) as stream:
        reader = PdfReader(stream)
        if len(reader.pages) > latex.MAX_FILES:
            raise ValueError("Too many PDF pages for SyncTeX")
        pages = [[float(page.mediabox.width), float(page.mediabox.height)] for page in reader.pages]
    if not pages or any(not math.isfinite(n) or n <= 0 or n > 100000 for page in pages for n in page):
        raise ValueError("Invalid PDF page dimensions")
    manifest = {"build": str(build), "sources": sources, "pages": pages,
                "pdf": digest(pdf, latex.MAX_OUTPUT), "synctex": digest(sync, latex.MAX_OUTPUT)}
    encoded = json.dumps(manifest).encode("utf-8")
    if len(encoded) > MAX_MANIFEST:
        raise ValueError("SyncTeX snapshot manifest exceeds 4 MiB")
    files.publish(pdf.parent / MANIFEST, encoded, 0o600)
    return True


def _stale(message="Compiled PDF changed; compile again", code="stale_pdf"):
    raise WorkspaceError(message, 409, code)


def _current(workspace: Path, requested):
    if not isinstance(requested, str) or latex.status(workspace)["compilation"].get("pdf") != requested:
        _stale()


def _snapshot(workspace: Path, requested):
    _current(workspace, requested)
    # Only the exact compiler-owned output layout is eligible, not arbitrary PDFs.
    if not re.fullmatch(r"latex-workspace/\.easyread-build/[a-f0-9]{32}/output-[^/]+/easyread\.pdf", requested):
        _stale()
    pdf = files.target(workspace, requested, missing=False)
    manifest_path = files.target(workspace, str(Path(requested).parent / MANIFEST))
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(files.read_bytes(manifest_path, MAX_MANIFEST))
        if digest(pdf, latex.MAX_OUTPUT) != manifest["pdf"]:
            _stale()
        sync = files.target(workspace, str(Path(requested).with_suffix(".synctex.gz")), missing=False)
        raw = files.read_bytes(sync, latex.MAX_OUTPUT)
        if hashlib.sha256(raw).hexdigest() != manifest["synctex"]:
            _stale()
        if not isinstance(manifest["sources"], dict) or len(manifest["sources"]) > latex.MAX_FILES:
            raise ValueError("Invalid snapshot")
        if not isinstance(manifest["build"], str) or not Path(manifest["build"]).is_absolute():
            raise ValueError("Invalid build path")
        pages = manifest["pages"]
        if not isinstance(pages, list) or not 0 < len(pages) <= latex.MAX_FILES:
            raise ValueError("Invalid pages")
        for page in pages:
            if len(page) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) or not 0 < n <= 100000 for n in page):
                raise ValueError("Invalid page dimensions")
        return manifest, raw
    except (ValueError, KeyError, TypeError):
        _stale("SyncTeX snapshot is invalid; compile again")


def _source(workspace: Path, manifest: dict, relative: str) -> Path | None:
    relative = files.relative(relative)
    if relative not in manifest["sources"]:
        return None
    if relative.split("/")[0] in (latex.BUILD_DIR, latex.STATE_FILE):
        return None
    project_path = "latex-workspace/" + relative
    source = files.target(workspace, project_path)
    if not source.is_file() or digest(source, latex.MAX_SOURCE) != manifest["sources"][relative]:
        _stale("Source changed since this PDF was compiled; compile again", "stale_source")
    return source


def _integer(value, name, minimum=1):
    if type(value) is not int or not minimum <= value <= 10000000:
        raise WorkspaceError("Invalid SyncTeX " + name)
    return value


def _number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise WorkspaceError("Invalid SyncTeX " + name)
    return value


def _query(raw: bytes, arguments) -> list[dict]:
    executable = shutil.which("synctex")
    if not executable:
        return []
    if not _QUERY_SLOTS.acquire(blocking=False):
        raise WorkspaceError("SyncTeX is busy; retry shortly", 409, "project_busy")
    try:
        with tempfile.TemporaryDirectory(prefix="easyread-synctex-") as tmp:
            directory = Path(tmp)
            # The utility needs an existing output name, not the PDF contents.
            # A private uncompressed copy prevents alternate-file discovery and
            # deletion by synctex, and bounds gzip expansion before its parser.
            output = directory / "easyread.pdf"
            output.touch()
            total = 0
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as src, (directory / "easyread.synctex").open("xb") as dest:
                    while chunk := src.read(65536):
                        total += len(chunk)
                        if total > latex.MAX_SOURCE:
                            raise WorkspaceError("SyncTeX expansion exceeds 128 MiB", 413)
                        dest.write(chunk)
            except (OSError, EOFError):
                _stale("Invalid SyncTeX data; compile again")
            command = [executable, *arguments(output)]
            if os.name == "posix" and shutil.which("prlimit"):
                command = [shutil.which("prlimit"), "--as=1073741824", "--cpu=5", "--", *command]
            process = subprocess.Popen(command, cwd=directory, env=latex._environment(directory),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            captured = bytearray()
            overflow = threading.Event()
            def collect():
                try:
                    while chunk := process.stdout.read(8192):
                        room = MAX_QUERY_OUTPUT - len(captured)
                        captured.extend(chunk[:room])
                        if len(chunk) > room:
                            overflow.set()
                            process.kill()
                finally:
                    process.stdout.close()
            reader = threading.Thread(target=collect, daemon=True)
            reader.start()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise WorkspaceError("SyncTeX query timed out", 408) from None
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                reader.join(timeout=5)
            if overflow.is_set():
                raise WorkspaceError("SyncTeX output exceeds size limit", 413)
            if process.returncode:
                raise WorkspaceError("SyncTeX query failed; compile again", 400, "sync_unavailable")
            records, record = [], None
            for line in captured.decode("utf-8", errors="replace").splitlines():
                if line.startswith("Output:"):
                    record = {}
                    records.append(record)
                elif record is not None and ":" in line:
                    key, value = line.split(":", 1)
                    record[key] = value
            return records[:200]
    finally:
        _QUERY_SLOTS.release()


def forward(workspace: Path, body: dict) -> dict:
    pdf = body.get("pdf")
    path = files.relative(body.get("path"))
    line = _integer(body.get("line"), "line")
    column = _integer(body.get("column", 1), "column", 0)
    if not path.startswith("latex-workspace/"):
        raise WorkspaceError("SyncTeX source must be inside latex-workspace")
    snapshot = _snapshot(workspace, pdf)
    result = {"found": False, "matches": [], "pdf": pdf}
    if not snapshot:
        return result
    manifest, raw = snapshot
    relative = path[len("latex-workspace/"):]
    if _source(workspace, manifest, relative) is None:
        return result
    original = str(Path(manifest["build"]) / relative)
    records = _query(raw, lambda output: ["view", "-i", f"{line}:{column}:{original}", "-o", str(output)])
    for record in records:
        try:
            page = int(record["Page"])
            if not 1 <= page <= len(manifest["pages"]):
                continue
            width, height = manifest["pages"][page - 1]
            x, bottom, w, h = [float(record[k]) for k in ("h", "v", "W", "H")]
            if not all(math.isfinite(n) for n in (x, bottom, w, h)) or w < 0 or h < 0:
                continue
            left, top = max(0, x), max(0, bottom - h)
            right, bottom = min(width, x + w), min(height, bottom)
            if right < left or bottom < top:
                continue
            result["matches"].append({"page": page, "x": left, "y": top,
                                      "width": right - left, "height": bottom - top,
                                      "page_width": width, "page_height": height})
        except (KeyError, ValueError):
            continue
    _current(workspace, pdf)
    _source(workspace, manifest, relative)
    result["found"] = bool(result["matches"])
    return result


def inverse(workspace: Path, body: dict) -> dict:
    pdf = body.get("pdf")
    page = _integer(body.get("page"), "page")
    x, y = _number(body.get("x"), "x"), _number(body.get("y"), "y")
    if x < 0 or y < 0:
        raise WorkspaceError("SyncTeX coordinates must be inside the PDF page")
    snapshot = _snapshot(workspace, pdf)
    result = {"found": False, "pdf": pdf}
    if not snapshot:
        return result
    manifest, raw = snapshot
    if page > len(manifest["pages"]):
        raise WorkspaceError("Invalid SyncTeX page")
    width, height = manifest["pages"][page - 1]
    if x > width or y > height:
        raise WorkspaceError("SyncTeX coordinates must be inside the PDF page")
    records = _query(raw, lambda output: ["edit", "-o", f"{page}:{x}:{y}:{output}"])
    original = Path(manifest["build"])
    for record in records:
        try:
            input_path = Path(record["Input"])
            # Lexical normalization only: never resolve or expose an external file.
            normalized = Path(os.path.normpath(input_path if input_path.is_absolute() else original / input_path))
            relative = normalized.relative_to(original).as_posix()
            line = _integer(int(record["Line"]), "line")
            column = _integer(max(1, int(record.get("Column", "1"))), "column")
        except (KeyError, ValueError):
            continue
        if _source(workspace, manifest, relative) is not None:
            _current(workspace, pdf)
            return {"found": True, "path": "latex-workspace/" + relative,
                    "line": line, "column": column, "pdf": pdf}
    _current(workspace, pdf)
    return result

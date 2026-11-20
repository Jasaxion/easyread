#!/usr/bin/env python3
"""Check research prerequisites or explicitly install verified bd/Dolt releases.

No repository initialization, shell installers, credential changes or package
manager invocation. TeX/system installation commands are printed for review.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

BEADS_VERSION = "1.3.4"
DOLT_VERSION = "2.7.5"
# Release asset SHA-256 digests, not hashes of extracted executables.
DOLT_SHA256 = {
    "linux-amd64": "ab4f57f6031da5123d4edb9fa43676d5fe50cd08d099cdf3e0ae632bf378dbba",
    "linux-arm64": "56d1fbf89f173ba386421f13d401efce1dc1bd50f7f981ce37479b6aaa548d69",
    "darwin-amd64": "e20c7b2964eacb983cb2bc17ec2e40dca40c47c38ffae01a97fd4325e662cdc9",
    "darwin-arm64": "b9d2bcd08f84b710c32ccfb3d87e5f91cddffccdc7c0f3b475ddbeacbb781416",
    "windows-amd64": "a51c2557ddd03ca284971957c0cb006e18585c4a063e83db7b94d0fd38814300",
}
TEX_PACKAGES = "texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended texlive-xetex texlive-luatex texlive-lang-chinese texlive-bibtex-extra latexmk biber"


def _fetch(url: str, destination: Path, limit: int = 256 * 1024 * 1024) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "EasyRead-workspace-installer"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
                size = 0
                while chunk := response.read(65536):
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("Release download exceeds size limit")
                    output.write(chunk)
            return
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def _version(value: str) -> str:
    value = value.removeprefix("v")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", value):
        raise ValueError("Use an explicit release version, for example 1.3.4")
    return value


def _platform() -> tuple[str, str]:
    system = platform.system().lower()
    architecture = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if system not in ("linux", "darwin", "windows") or not architecture:
        raise ValueError("Use the upstream package manager for this platform")
    return system, architecture


def _extract_binary(archive: Path, binary: str, target: Path) -> None:
    """Read one regular member; never extract archive paths, links or hooks."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".workspace-install-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output:
            if archive.suffix == ".zip":
                with zipfile.ZipFile(archive) as source:
                    matches = [m for m in source.infolist() if Path(m.filename).name == binary and not m.is_dir()]
                    if len(matches) != 1 or matches[0].file_size > 512 * 1024 * 1024:
                        raise ValueError("Release archive does not contain one bounded binary")
                    with source.open(matches[0]) as stream:
                        shutil.copyfileobj(stream, output)
            else:
                with tarfile.open(archive, "r:gz") as source:
                    matches = [m for m in source.getmembers() if Path(m.name).name == binary and m.isfile()]
                    if len(matches) != 1 or matches[0].size > 512 * 1024 * 1024:
                        raise ValueError("Release archive does not contain one bounded binary")
                    with source.extractfile(matches[0]) as stream:
                        shutil.copyfileobj(stream, output)
        temporary.chmod(0o755)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def install(prefix: Path, beads_version: str, dolt_version: str, dolt_sha256: str = "") -> None:
    system, arch = _platform()
    extension = "zip" if system == "windows" else "tar.gz"
    beads_version, dolt_version = _version(beads_version), _version(dolt_version)
    with tempfile.TemporaryDirectory(prefix="easyread-deps-") as directory:
        root = Path(directory)
        for tool, repo, version in (("bd", "gastownhall/beads", beads_version), ("dolt", "dolthub/dolt", dolt_version)):
            name = f"beads_{version}_{system}_{arch}.{extension}" if tool == "bd" else f"dolt-{system}-{arch}.{extension}"
            base = f"https://github.com/{repo}/releases/download/v{version}"
            if tool == "bd":
                checksum = root / "checksums.txt"
                _fetch(base + "/checksums.txt", checksum, 1024 * 1024)
                lines = [line.split()[0] for line in checksum.read_text().splitlines()
                         if len(line.split()) == 2 and line.split()[1].lstrip("*") == name]
                if len(lines) != 1:
                    raise ValueError(f"No unique checksum for {name}")
                digest = lines[0]
            else:
                digest = dolt_sha256 or (DOLT_SHA256.get(f"{system}-{arch}", "") if version == DOLT_VERSION else "")
                if not digest:
                    raise ValueError("This Dolt release/platform requires --dolt-sha256 from its official release asset")
            if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
                raise ValueError("Invalid SHA-256 digest")
            archive = root / name
            print(f"Downloading {name}", flush=True)
            _fetch(base + "/" + name, archive)
            with archive.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()
            if actual.lower() != digest.lower():
                raise ValueError(f"Checksum mismatch: {name}")
            binary = tool + (".exe" if system == "windows" else "")
            target = prefix / "bin" / binary
            _extract_binary(archive, binary, target)
            subprocess.run([str(target), "version"], check=True, timeout=30)
        notices = prefix / "share/easyread-workspace-deps"
        notices.mkdir(parents=True, exist_ok=True)
        for name, repo, version in (("beads", "gastownhall/beads", beads_version), ("dolt", "dolthub/dolt", dolt_version)):
            _fetch(f"https://raw.githubusercontent.com/{repo}/v{version}/LICENSE", notices / f"{name}-LICENSE", 1024 * 1024)
        (notices / "versions.json").write_text(json.dumps({"beads": beads_version, "dolt": dolt_version}, indent=2))
    print(f"Installed native tools in {prefix / 'bin'}. Add this directory to PATH and restart EasyRead.")


def check() -> dict:
    tools = {}
    for tool in ("git", "bd", "dolt", "pdflatex", "xelatex", "lualatex", "latexmk", "synctex", "codex", "claude", "ttyd"):
        override = "EASYREAD_TEX_" + tool.upper() if tool in ("pdflatex", "xelatex", "lualatex") else None
        executable = shutil.which(os.environ.get(override, tool) if override else tool)
        tools[tool] = {"installed": bool(executable), "path": executable}
    tools["mcp"] = {"installed": importlib.util.find_spec("mcp") is not None}
    return tools


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check only (also the default); no downloads or version commands")
    parser.add_argument("--install", action="store_true", help="Explicitly download/install checksum-verified bd and Dolt")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    parser.add_argument("--beads-version", default=os.environ.get("BEADS_VERSION", BEADS_VERSION))
    parser.add_argument("--dolt-version", default=os.environ.get("DOLT_VERSION", DOLT_VERSION))
    parser.add_argument("--dolt-sha256", default=os.environ.get("DOLT_SHA256", ""))
    args = parser.parse_args()
    if args.check and args.install:
        parser.error("Choose --check or --install")
    if args.install:
        install(args.prefix.expanduser().absolute(), args.beads_version, args.dolt_version, args.dolt_sha256)
    print(json.dumps(check(), indent=2))
    print("TeX + Git (explicit system install; may download several GB):")
    print("  Debian/Ubuntu: sudo apt-get update && sudo apt-get install -y git " + TEX_PACKAGES)
    print("  macOS: brew install git beads dolt && brew install --cask mactex-no-gui")
    print("  Windows: winget install Git.Git; winget install MiKTeX.MiKTeX (then install needed packages in MiKTeX Console)")
    print('  Source MCP: python -m pip install "mcp>=1.26,<2" (use the same Python as EasyRead)')
    print("  CLI: see https://developers.openai.com/codex/cli/ and https://code.claude.com/docs/en/setup")
    print("Desktop: TeX, Git and Agent CLIs are external dependencies; restart the app after changing PATH.")


if __name__ == "__main__":
    main()

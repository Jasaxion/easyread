"""Project-scoped MCP server for one research project, spoken over stdio.

Launched by the native CLI session (``session_cli.mcp_command``) as
``python -m easyread.workspaces.mcp --library <root> --project <id>``, so the
agent working in a project can search and read the EasyRead library and link
papers to the project it is running in — and to nothing else.

Reading reuses :class:`easyread.server.public_api.PublicAPI` through a light
context shim (no HTTP listener, no import/background jobs, no access gate: stdio
is already private to the spawned CLI). Linking delegates to the workspace store.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
INSTRUCTIONS = ("EasyRead research project tools. Search and read the user's paper library "
                "(metadata, translated content, notes, highlights) and manage this project's "
                "linked papers. Links are references: the library keeps owning the documents. "
                "Read-only tools never change the library.")
DESCRIPTIONS = {
    "list_documents": "Search the EasyRead library. Filter by free text (q), category (includes descendants) "
                      "and reading status; paginate with limit/offset.",
    "get_document": "Get one document's metadata, reading progress and links by document_id.",
    "get_content": "Read ordered content blocks of a document: language=original|translation|both, "
                   "view=effective (with user edits) or raw. Paginate with limit/offset.",
    "get_notes": "Read a document's paper-wide and paragraph notes and questions.",
    "get_highlights": "Read a document's highlights with quote, colour and anchor.",
    "list_project_papers": "List papers linked to this research project, plus library papers available to link "
                           "(optionally filtered by q).",
    "link_paper": "Link a library paper to this research project by document_id (a reference, not a copy).",
    "unlink_paper": "Remove the link between a library paper and this research project.",
    "get_project": "Get this research project's name, description and engine.",
}


class _Access:
    enabled = False


class _Location:
    """Minimal stand-in for the server's library location guard."""

    @contextmanager
    def request(self, method, raw_path):
        yield


class _App:
    """The slice of the server app that PublicAPI's read methods actually use."""

    def __init__(self, libroot):
        from ..library.library import Library
        self.lib = Library(Path(libroot))
        self.access = _Access()
        self.location = _Location()
        self.token = ""


def public_api(libroot):
    from ..server.public_api import PublicAPI
    return PublicAPI(_App(libroot), {})


def make_store(libroot):
    from .store import Store
    return Store(Path(libroot))


def build(libroot, pid: str, *, api=None, store=None):
    """Create the FastMCP server for one project without starting a transport."""
    from mcp.server.fastmcp import FastMCP
    from mcp.types import CallToolResult, TextContent, ToolAnnotations
    from ..server.public_api import APIError

    libroot = Path(libroot)
    api = api if api is not None else public_api(libroot)
    store = store if store is not None else make_store(libroot)
    store.get(pid)  # Fail before publishing any library tools for a nonexistent project.
    resolve = lambda: store
    server = FastMCP("EasyRead research project", instructions=INSTRUCTIONS)

    def guard(fn):
        @wraps(fn)
        def invoke(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except APIError as error:
                return _failure(CallToolResult, TextContent, error.body())
            except ValueError as error:
                return _failure(CallToolResult, TextContent,
                                {"error": str(error), "code": getattr(error, "code", "invalid_request")})
        return invoke

    def list_project_papers(q: str = ""):
        return resolve().papers(pid, q)

    def link_paper(document_id: str):
        return resolve().link(pid, document_id)

    def unlink_paper(document_id: str):
        return resolve().link(pid, document_id, remove=True)

    def get_project():
        project = resolve().get(pid) or {}
        return {k: project.get(k) for k in ("id", "name", "description", "engine", "created", "updated")}

    writers = {"link_paper", "unlink_paper"}
    for name, fn in (("list_documents", api.documents), ("get_document", api.document),
                     ("get_content", api.content), ("get_notes", api.notes),
                     ("get_highlights", api.highlights), ("list_project_papers", list_project_papers),
                     ("link_paper", link_paper), ("unlink_paper", unlink_paper),
                     ("get_project", get_project)):
        server.add_tool(guard(fn), name=name, description=DESCRIPTIONS[name],
                        annotations=ToolAnnotations(readOnlyHint=name not in writers, destructiveHint=False,
                                                    openWorldHint=False))
    return server


def _failure(result_cls, text_cls, body: dict):
    payload = json.dumps(body, ensure_ascii=False)
    return result_cls(isError=True, content=[text_cls(type="text", text=payload)], structuredContent=body)


def parse(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="easyread-workspace-mcp",
                                     description="Project-scoped EasyRead MCP server (stdio).")
    parser.add_argument("--workspace-mcp", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--library", required=True, help="EasyRead library root directory")
    parser.add_argument("--project", required=True, help="Research project id")
    args = parser.parse_args(list(argv))
    if not PROJECT_ID.match(args.project or ""):
        parser.error("invalid --project id")
    root = Path(args.library)
    if not root.is_dir():
        parser.error("library root does not exist")
    args.library = root
    return args


def main(argv=None) -> int:
    args = parse(sys.argv[1:] if argv is None else argv)
    try:
        server = build(args.library, args.project)
    except ValueError as error:
        print(f"easyread workspace MCP unavailable: {error}", file=sys.stderr)
        return 2
    except ImportError as error:  # The MCP dependency is optional in lite images.
        print(f"easyread workspace MCP unavailable: {error}", file=sys.stderr)
        return 3
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Version 2 archives of project-owned durable native CLI state.

This is not a CLI-home clone. Credentials/configuration (including their backups),
user capabilities, environment snapshots, process registries/locks, diagnostic
logs and regenerable caches are excluded by name, even when replaced by real
files. Unknown top-level layouts and unknown SQLite schemas fail closed.

Native UUIDs and opaque file-history/attachment bytes survive unchanged. The
versioned adapters relocate structured paths and SQLite indexes, including Codex
rollout byte offsets. Import only publishes into a new, empty project's private
homes; it never launches a CLI, prepares authentication, or executes config.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import time

from . import session_cli as cli
from . import files, session_reasoning
from .session_cli import SessionError
from .session_store import expire

KIND = "easyread-workspace-sessions"
VERSION = 2
MAX_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 50000
SID = re.compile(r"[a-z0-9]{6,32}")
ADAPTERS = {"codex": "codex-durable-state-1", "claude": "claude-project-state-1", "pi": "pi-session-jsonl-v3-1"}
EXCLUSIONS = [
    "Host authentication, configuration, configuration backups and inherited capabilities",
    "Shell/environment snapshots and runtime process registries/locks",
    "Telemetry, diagnostics, debug/usage logs and regenerable model/application caches",
    "External tool/service state outside the project's private native home",
]
DURABLE_DIRS = {
    "codex": {"sessions", "archived_sessions", "tasks", "subagents", "file-history", "memories", "attachments"},
    "claude": {"projects", "tasks", "todos", "file-history", "session-memory", "plans"},
    "pi": {"sessions"},
}
DURABLE_FILES = {"codex": {"history.jsonl", "session_index.jsonl"}, "claude": {"history.jsonl"}, "pi": set()}
EXCLUDED = {
    "pi": {".inheritance.json", "auth.json", "settings.json", "models.json", "mcp.json", "trust.json"},
    "codex": {".inheritance.json", ".sandbox_migration", "models_cache.json", "version.json", "cache", "log",
              "logs_2.sqlite", "thread-writer-locks", "shell_snapshots", "tmp", ".tmp", "feedback"},
    "claude": {".inheritance.json", ".last-cleanup", "backups", "session-env", "session-data", "sessions",
               "shell-snapshots", "telemetry", "debug", "metrics", "bash-commands.log", "cost-tracker.log",
               "stats-cache.json", "statsig", "cache", "downloads", "ide", "paste-cache"},
}
SQLITE_TABLES = {
    "state_5.sqlite": {"threads", "thread_dynamic_tools", "backfill_state", "thread_spawn_edges",
                       "remote_control_enrollments", "external_agent_config_imports", "thread_sections",
                       "rollout_migration_state", "rollout_migration_skipped_rollouts", "projects", "project_roots",
                       "project_idempotency_keys", "thread_attachments", "guardian_review_feedback", "thread_read_receipts"},
    "goals_1.sqlite": {"thread_goals", "thread_goal_continuation_deferrals"},
    "memories_1.sqlite": {"stage1_outputs", "jobs", "consolidation_progress"},
    "queue_1.sqlite": {"queued_items", "queued_thread_revisions"},
    "thread_history_1.sqlite": {"thread_turns", "thread_items", "thread_history_projection_state", "thread_realtime_items"},
}
HOST_TABLES = {"remote_control_enrollments", "external_agent_config_imports"}
PATH_COLUMNS = {"cwd", "rollout_path", "path", "agent_path"}
STRUCTURED_COLUMNS = {"payload_json", "payload", "metadata", "sandbox_policy", "input_schema", "item_json", "error_json"}
TEXT_COLUMNS = {"raw_memory", "rollout_summary", "title", "first_user_message", "preview"}
OFFSET_COLUMNS = {"rollout_byte_offset", "rollout_end_byte_offset", "next_rollout_byte_offset"}


# Codex state_5's own timestamp maintenance triggers. Import changes only path
# columns, never these trigger columns; arbitrary imported trigger SQL is rejected.
SQLITE_TRIGGERS = {}
for _field in ("created_at", "updated_at"):
    for _operation in ("insert", "update"):
        _name = f"threads_{_field}_ms_after_{_operation}"
        _when = (f"NEW.{_field}_ms IS NULL" if _operation == "insert" else
                 f"NEW.{_field} != OLD.{_field} AND NEW.{_field}_ms IS OLD.{_field}_ms")
        _event = "INSERT" if _operation == "insert" else f"UPDATE OF {_field}"
        SQLITE_TRIGGERS[_name] = (f"CREATE TRIGGER {_name} AFTER {_event} ON threads WHEN {_when} BEGIN "
                                 f"UPDATE threads SET {_field}_ms = NEW.{_field} * 1000 WHERE id = NEW.id; END")
SQLITE_TRIGGERS["threads_recency_at_after_insert"] = (
    "CREATE TRIGGER threads_recency_at_after_insert AFTER INSERT ON threads WHEN NEW.recency_at_ms = 0 BEGIN "
    "UPDATE threads SET recency_at = NEW.updated_at, recency_at_ms = COALESCE(NEW.updated_at_ms, NEW.updated_at * 1000) "
    "WHERE id = NEW.id; END")

TRIGGER_DATABASES = {name: "state_5.sqlite" for name in SQLITE_TRIGGERS}
SQLITE_TRIGGERS["thread_realtime_items_projection_cleanup"] = (
    "CREATE TRIGGER thread_realtime_items_projection_cleanup AFTER DELETE ON thread_history_projection_state "
    "BEGIN DELETE FROM thread_realtime_items WHERE thread_id = OLD.thread_id; END")
TRIGGER_DATABASES["thread_realtime_items_projection_cleanup"] = "thread_history_1.sqlite"
for _operation in ("insert", "update", "delete"):
    _name = f"queued_items_revision_after_{_operation}"
    _row = "OLD" if _operation == "delete" else "NEW"
    SQLITE_TRIGGERS[_name] = (
        f"CREATE TRIGGER {_name} AFTER {_operation.upper()} ON queued_items BEGIN "
        f"INSERT INTO queued_thread_revisions (thread_id) VALUES ({_row}.thread_id) "
        "ON CONFLICT(thread_id) DO UPDATE SET revision = "
        "(SELECT COALESCE(MAX(revision), 0) + 1 FROM queued_thread_revisions); END")
    TRIGGER_DATABASES[_name] = "queue_1.sqlite"


def fail(message, code="unsupported_native_state"):
    raise SessionError(code, message, 409)


def _excluded(engine, name):
    base = re.sub(r"-(wal|shm|journal)$", "", name)
    inherited = cli.SPECS[engine]["links"]
    return (base in EXCLUDED[engine] or name in inherited
            or any(name.startswith(key + ".") for key in inherited)
            or name.endswith(".inherit-tmp"))


def _kind(engine, relative):
    top = relative.split("/")[0]
    if _excluded(engine, top):
        return "excluded"
    if engine == "pi" and top == "sessions":
        if relative == "sessions" or re.fullmatch(r"sessions/[A-Za-z0-9T_.:-]+\.jsonl", relative):
            return "durable"
        fail("Unsupported Pi session path: " + relative)
    if top in DURABLE_DIRS[engine] or top in DURABLE_FILES[engine]:
        return "durable"
    if engine == "codex" and re.sub(r"-(wal|shm|journal)$", "", top) in SQLITE_TABLES:
        return "sqlite"
    fail("Unsupported native home entry: " + relative)


def _stat(path):
    with files.directory(path.parent) as parent:
        name, kwargs = files.at(path, parent)
        value = os.stat(name, follow_symlinks=False, **kwargs)
    if not (stat.S_ISREG(value.st_mode) or stat.S_ISDIR(value.st_mode)) or (stat.S_ISREG(value.st_mode) and value.st_nlink != 1):
        fail("Native state contains a link or special file: " + path.name, "invalid_archive")
    return value


def _walk(root):
    """No-follow traversal, including empty directories and file modes."""
    _stat(root)
    def visit(path):
        with files.directory(path) as fd:
            with os.scandir(fd if fd is not None else path) as entries:
                names = sorted(entry.name for entry in entries)
        for name in names:
            child = path / name
            metadata = _stat(child)
            yield child, metadata
            if stat.S_ISDIR(metadata.st_mode):
                yield from visit(child)
    yield from visit(root)


def _copy_regular(source, target, budget):
    target.parent.mkdir(parents=True, exist_ok=True)
    with files.open_regular(source) as src, target.open("xb") as dst:
        before = os.fstat(src.fileno())
        while chunk := src.read(min(1024 * 1024, MAX_BYTES - budget[0] + 1)):
            budget[0] += len(chunk)
            if budget[0] > MAX_BYTES:
                fail("Native state exceeds archive size limit", "too_large")
            dst.write(chunk)
        after = os.fstat(src.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            fail("Native file changed during export/import")


def _connect(path, readonly=False):
    db = sqlite3.connect(path.absolute().as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=2)
    db.execute("PRAGMA trusted_schema=OFF")
    if hasattr(db, "enable_load_extension"):
        db.enable_load_extension(False)
    return db


@contextmanager
def _database(path, readonly=False):
    try:
        with closing(_connect(path, readonly)) as db:
            yield db
    except sqlite3.Error:
        fail("Native SQLite data is invalid or unsupported: " + path.name, "invalid_archive")


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _schema(db, name):
    allowed = SQLITE_TABLES[name] | {"_sqlx_migrations", "sqlite_sequence"}
    tables = {}
    for kind, table, sql in db.execute("SELECT type,name,sql FROM sqlite_master"):
        if kind == "index":
            continue
        if (kind == "trigger" and TRIGGER_DATABASES.get(table) == name and table in SQLITE_TRIGGERS
                and " ".join((sql or "").split()) == " ".join(SQLITE_TRIGGERS[table].split())):
            continue
        if kind != "table" or table not in allowed or "VIRTUAL TABLE" in (sql or "").upper():
            fail("Unsupported native SQLite schema in " + name)
        columns = list(db.execute("PRAGMA table_info(" + _quote(table) + ")"))
        if not columns:
            fail("Invalid native SQLite table in " + name)
        tables[table] = [column[1] for column in columns]
    if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
        fail("Invalid native SQLite database: " + name)
    for table in HOST_TABLES & tables.keys():
        # No credential-bearing host rows are ever put in a project archive.
        if db.execute("SELECT 1 FROM " + _quote(table) + " LIMIT 1").fetchone():
            fail("Native SQLite contains host configuration; refusing full export/import")
    return tables


def _inside(value, root):
    value, root = value.replace("\\", "/"), str(root).replace("\\", "/").rstrip("/")
    return value == root or value.startswith(root + "/")


def _database_scope(db, tables, root):
    for table, column in (("threads", "cwd"), ("project_roots", "path")):
        if table in tables:
            if column not in tables[table]:
                fail("Native SQLite is missing project ownership metadata")
            for (value,) in db.execute("SELECT " + _quote(column) + " FROM " + _quote(table)):
                if not isinstance(value, str) or not _inside(value, root):
                    fail("Native SQLite contains state from another project")


def _snapshot_database(source, target, budget):
    """Copy main+WAL without opening the host DB; back up the isolated snapshot."""
    with tempfile.TemporaryDirectory(prefix="easyread-native-sqlite-") as tmp:
        copy = Path(tmp).resolve() / source.name
        candidates = [source] + [source.with_name(source.name + suffix) for suffix in ("-wal", "-shm", "-journal")]
        before = {}
        for path in candidates:
            if path.exists() or path.is_symlink():
                metadata = _stat(path)
                before[path] = (metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
                if path.name.endswith("-journal") and metadata.st_size:
                    fail("Native SQLite has an unfinished rollback journal")
                # SHM is a regenerable WAL index, not database content.
                if not path.name.endswith("-shm"):
                    _copy_regular(path, Path(tmp).resolve() / path.name, budget)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=False)
        deadline = time.monotonic() + 10
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                fail("Native SQLite snapshot timed out")
        with _database(copy, True) as src, _database(target) as dst:
            src.backup(dst, pages=256, progress=progress, sleep=.01)
            dst.execute("PRAGMA journal_mode=DELETE")  # Standalone bundle: no WAL/SHM runtime files.
        after = {path: (s.st_ino, s.st_size, s.st_mtime_ns) for path in candidates
                 if path.exists() or path.is_symlink() for s in [_stat(path)]}
        if before != after:
            fail("Native SQLite changed during export")


def _transcript_identity(engine, path, root):
    text = cli.read_transcript(path)
    records = cli.validate_transcript(engine, text)
    if not _inside(cli._old_cwd(records), root):
        fail("Native transcript belongs to another project")
    if engine == "pi":
        native_id = records[0]["id"]
        if not path.name.endswith("_" + native_id + ".jsonl") or Path(records[0]["cwd"]).resolve() != Path(root).resolve():
            fail("Invalid Pi session ownership or filename")
        return native_id
    if engine == "codex":
        meta = next(r["payload"] for r in records if r["type"] == "session_meta")
        return meta.get("id") or meta["session_id"]
    return next(r["sessionId"] for r in records if "sessionId" in r)


def _inspect_home(engine, home, root):
    """Ownership proof for all native conversations, not only displayed records."""
    ids = set()
    for path, metadata in _walk(home):
        relative = path.relative_to(home).as_posix()
        if engine == "pi":
            _kind(engine, relative)
            if stat.S_ISREG(metadata.st_mode):
                native_id = _transcript_identity(engine, path, root)
                if native_id in ids:
                    fail("Duplicate Pi session identity")
                ids.add(native_id)
            continue
        if not stat.S_ISREG(metadata.st_mode):
            continue
        if engine == "codex" and relative.startswith(("sessions/", "archived_sessions/")) and path.name.startswith("rollout-") and path.suffix == ".jsonl":
            ids.add(_transcript_identity(engine, path, root))
        elif (engine == "claude" and relative.startswith("projects/") and path.suffix == ".jsonl"
              and ((len(relative.split("/")) == 3 and cli.valid_native_id(path.stem))
                   or (path.parent.name == "subagents" and path.name.startswith("agent-")))):
            ids.add(_transcript_identity(engine, path, root))
    if engine == "claude" and (home / "projects").exists():
        for folder in (home / "projects").iterdir():
            if folder.name != cli.claude_slug(root):
                fail("Native Claude state contains another project directory")
    return ids


def _format(engine, relative):
    if relative in SQLITE_TABLES and engine == "codex":
        return "sqlite"
    if set(relative.split("/")[:-1]) & {"file-history", "attachments", "tool-results"}:
        return "opaque"
    if relative.endswith(".md") and set(relative.split("/")[:-1]) & {"memory", "memories", "session-memory", "plans"}:
        return "text"
    if relative.endswith(".jsonl"):
        return "jsonl"
    if relative.endswith(".json"):
        return "json"
    return "opaque"


def _inventory(root):
    entries = []
    total = 0
    for path, metadata in _walk(root):
        if len(entries) >= MAX_ENTRIES:
            fail("Native state has too many entries", "too_large")
        entry = {"path": path.relative_to(root).as_posix(), "mode": stat.S_IMODE(metadata.st_mode) & 0o777,
                 "kind": "directory" if stat.S_ISDIR(metadata.st_mode) else "file"}
        if entry["kind"] == "file":
            raw = files.read_bytes(path, MAX_BYTES)
            total += len(raw)
            if total > MAX_BYTES:
                fail("Native state exceeds archive size limit", "too_large")
            entry.update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        entries.append(entry)
    return entries


def export_state(sessions, pid, dest):
    store, dest = sessions.store, Path(dest)
    store.get(pid)
    with store.lock(pid):
        if sessions.busy(pid):
            fail("Close native terminals and wait for turns before exporting", "conflict")
        data = sessions._load(pid, full=True)
        records = [deepcopy(data["sessions"][sid]) for sid in data["order"]]
        if any(not r.get("cli_isolated", cli.isolation_enabled()) for r in records):
            fail("Full native export requires project-private CLI homes", "native_isolation_required")
        if dest.exists() or dest.is_symlink():
            metadata = _stat(dest)
            if not stat.S_ISDIR(metadata.st_mode) or any(dest.iterdir()):
                fail("Session export destination must be an empty directory", "conflict")
        root, meta = Path(store.root(pid)), Path(store.meta(pid))
        with tempfile.TemporaryDirectory(prefix="easyread-native-export-") as tmp:
            stage = Path(tmp).resolve()
            engines, budget, native_count = {}, [0], 0
            for engine in cli.ENGINES:
                home = cli.project_home(meta, engine)
                engine_records = [r for r in records if r["engine"] == engine]
                if not home.exists() and not home.is_symlink():
                    if any(r.get("native_started") or r.get("resumed") for r in engine_records):
                        fail("Native home is missing for a recorded session")
                    continue
                _stat(home)
                target = stage / "native" / engine
                target.mkdir(parents=True)
                modes = [(target, stat.S_IMODE(home.stat().st_mode) & 0o777)]
                excluded = []
                for child in sorted(home.iterdir()):
                    kind = _kind(engine, child.name)
                    if kind == "excluded":
                        excluded.append(child.name)
                        continue
                    metadata = _stat(child)
                    if kind == "sqlite":
                        if child.name not in SQLITE_TABLES:
                            if not (home / re.sub(r"-(wal|shm|journal)$", "", child.name)).is_file():
                                fail("Native SQLite sidecar has no main database")
                            continue  # Captured atomically with its main database.
                        _snapshot_database(child, target / child.name, budget)
                        modes.append((target / child.name, stat.S_IMODE(metadata.st_mode) & 0o777))
                        continue
                    entries = [(child, metadata)]
                    if stat.S_ISDIR(metadata.st_mode):
                        entries += list(_walk(child))
                    if len(entries) > MAX_ENTRIES:
                        fail("Native state has too many entries", "too_large")
                    for path, info in entries:
                        output = target / path.relative_to(home)
                        if stat.S_ISDIR(info.st_mode):
                            output.mkdir(parents=True, exist_ok=True)
                        else:
                            _copy_regular(path, output, budget)
                        modes.append((output, stat.S_IMODE(info.st_mode) & 0o777))
                ids = _inspect_home(engine, target, root)
                offsets = {}
                for path, info in _walk(target):
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    relative = path.relative_to(target).as_posix()
                    format = _format(engine, relative)
                    if format == "sqlite":
                        continue
                    mapping = _retarget_file(path, format, lambda value: value, validate_only=True)
                    if engine == "codex" and path.name.startswith("rollout-") and path.suffix == ".jsonl":
                        offsets[_transcript_identity(engine, path, root)] = mapping
                text, structured = _relocator(str(root), root / "relocation-check", str(home), home / "relocation-check")
                schemas = {}
                orphan_threads, orphan_fields = set(), 0
                for db_path in target.glob("*.sqlite"):
                    diagnostics = _retarget_database(db_path, root, text, structured, offsets, validate_only=True)
                    orphan_threads.update(diagnostics["orphan_thread_ids"])
                    orphan_fields += diagnostics["orphan_offset_fields"]
                    with _database(db_path, True) as db:
                        tables = _schema(db, db_path.name)
                        _database_scope(db, tables, root)
                        schemas[db_path.name] = tables
                for record in engine_records:
                    native_id = record.get("native_id") or ""
                    if native_id:
                        path = cli.transcript(engine, target, native_id, root)
                        if path:
                            record["native"] = path.relative_to(stage).as_posix()
                            native_count += 1
                        elif record.get("native_started") or record.get("resumed"):
                            fail("Native transcript is missing for a recorded session")
                    record["native_complete"] = True
                engines[engine] = {"adapter": ADAPTERS[engine], "source_home": str(home.absolute()),
                                   "native_ids": sorted(ids), "sqlite_schemas": schemas, "excluded_entries": excluded,
                                   "diagnostics": {"orphan_thread_ids": sorted(orphan_threads),
                                                   "orphan_offset_fields": orphan_fields}}
                for path, mode in reversed(modes):
                    path.chmod(mode)
            for record in records:
                record["cli_isolated"] = True
                record["native_complete"] = True
            _validate_records(sessions, records)
            manifest = {"kind": KIND, "version": VERSION, "exported": cli.now_iso(), "source_root": str(root.absolute()),
                        "native_complete": True, "native_scope": "project-owned-durable-state",
                        "exclusions": EXCLUSIONS, "engines": engines, "sessions": records,
                        "deletion_note": "Ordinary session deletion is not secure erasure of retained native database/task state or project Git history. Orphan native indexes do not recreate deleted UI conversations.",
                        "relocation": {"json_jsonl": "structured path values, stable native UUIDs",
                                       "sqlite": "versioned schemas, known path fields and rollout byte offsets",
                                       "memory_markdown": "project/native-home path references",
                                       "other_files": "opaque bytes, never interpreted or executed"},
                        "sequence": data["seq"], "entries": _inventory(stage)}
            raw_manifest = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
            if len(raw_manifest) > files.MAX_FILE or len(raw_manifest) + sum(entry.get("size", 0) for entry in manifest["entries"]) > MAX_BYTES:
                fail("Session history exceeds archive size limit", "too_large")
            (stage / "manifest.json").write_bytes(raw_manifest)
            dest.mkdir(parents=True, exist_ok=True)
            for path in stage.iterdir():
                shutil.move(str(path), dest / path.name)
    return {"sessions": len(records), "native": native_count, "native_complete": True, "path": str(dest)}


def _relocator(old_root, new_root, old_home, new_home):
    replacements = []
    for old, new in ((old_home, str(new_home)), (old_root, str(new_root))):
        for spelling in dict.fromkeys((old, old.replace("\\", "/"), old.replace("/", "\\"))):
            replacements.append((spelling, new))
    replacements.append((cli.claude_slug(old_root), cli.claude_slug(new_root)))
    def text(value):
        for old, new in replacements:
            # A project-root prefix must not retarget a neighboring project's name.
            value = re.sub(re.escape(old) + r"(?=$|[\\/\s\"'<>:;,)}\]])", lambda _: new, value)
        return value
    def path_value(value):
        for old, new in replacements:
            if value == old or value.startswith((old + "/", old + "\\")):
                suffix = value[len(old):].replace("\\", os.sep).replace("/", os.sep)
                return new.rstrip("/\\") + suffix
        return text(value)
    def structured(value):
        if isinstance(value, str):
            return text(value)
        if isinstance(value, list):
            return [structured(item) for item in value]
        if isinstance(value, dict):
            if value.get("type") in ("thinking", "redacted_thinking"):
                return value  # Signed native reasoning must remain byte-for-byte intact.
            path_keys = PATH_COLUMNS | {"file", "filePath", "workingDirectory", "working_directory", "projectRoot", "root"}
            return {path_value(key): path_value(item) if key in path_keys and isinstance(item, str) else structured(item)
                    for key, item in value.items()}
        return value
    return text, structured


def _retarget_file(path, format, structured, *, validate_only=False):
    raw = files.read_bytes(path, MAX_BYTES)
    if format == "opaque":
        return None
    try:
        if format == "text":
            rewritten = structured(raw.decode("utf-8"))
            if not validate_only:
                path.write_text(rewritten, encoding="utf-8")
            return None
        if format == "json":
            old = json.loads(raw)
            new = structured(old)
            if old != new and not validate_only:
                path.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
            return None
        output, offsets, before, after = [], {0: 0}, 0, 0
        for line in raw.splitlines(keepends=True):
            if not line.strip():
                rewritten = line
            else:
                old = json.loads(line)
                new = structured(old)
                rewritten = (json.dumps(new, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                             + (b"\n" if line.endswith(b"\n") else b"")) if old != new else line
            output.append(rewritten)
            before, after = before + len(line), after + len(rewritten)
            offsets[before] = after
        if not validate_only:
            path.write_bytes(b"".join(output))
        return offsets
    except (ValueError, UnicodeDecodeError):
        fail("Unsupported structured native file: " + path.name, "invalid_archive")


def _retarget_database(path, old_root, text, structured, offsets, *, validate_only=False):
    orphan_threads, orphan_fields = set(), 0
    with _database(path, validate_only) as db:
        tables = _schema(db, path.name)
        _database_scope(db, tables, old_root)
        with db:
            triggers = list(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
            if not validate_only:
                for name, _ in triggers:
                    db.execute("DROP TRIGGER " + _quote(name))
            for table, columns in tables.items():
                if table in {"_sqlx_migrations", "sqlite_sequence"}:
                    continue
                # Known adapters use ordinary rowid tables; no imported triggers/views execute.
                for row in db.execute("SELECT rowid,* FROM " + _quote(table)).fetchall():
                    values = dict(zip(columns, row[1:]))
                    updates = {}
                    for column, value in values.items():
                        new = value
                        if isinstance(value, str):
                            if column in STRUCTURED_COLUMNS:
                                try:
                                    parsed = json.loads(value)
                                except ValueError:
                                    fail("Invalid structured SQLite field: " + table + "." + column)
                                relocated = structured(parsed)
                                if relocated != parsed:
                                    new = json.dumps(relocated, ensure_ascii=False, separators=(",", ":"))
                            elif column in PATH_COLUMNS:
                                new = structured({column: value})[column]
                            elif column in TEXT_COLUMNS:
                                new = text(value)
                            elif text(value) != value:
                                fail("Unsupported path-bearing SQLite field: " + table + "." + column)
                        elif column in OFFSET_COLUMNS and value is not None:
                            thread = values.get("thread_id")
                            if not cli.valid_native_id(thread):
                                fail("Invalid native rollout offset identity")
                            if thread not in offsets:
                                # A deleted rollout has no bytes to retarget. Keep its
                                # retained native index data without creating a UI record.
                                orphan_threads.add(thread)
                                orphan_fields += 1
                            elif value not in offsets[thread]:
                                fail("Unsupported native rollout byte offset")
                            else:
                                new = offsets[thread][value]
                        if new != value:
                            updates[column] = new
                    if updates and not validate_only:
                        statement = "UPDATE " + _quote(table) + " SET " + ",".join(_quote(key) + "=?" for key in updates) + " WHERE rowid=?"
                        db.execute(statement, [*updates.values(), row[0]])
            for _, sql in triggers if not validate_only else []:
                db.execute(sql)  # Validated adapter DDL, not arbitrary imported SQL.
    return {"orphan_thread_ids": sorted(orphan_threads), "orphan_offset_fields": orphan_fields}


def _validate_records(sessions, records):
    if not isinstance(records, list) or len(records) > 60:
        fail("Invalid session records", "invalid_archive")
    seen, native_ids = set(), set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not SID.fullmatch(record["id"]):
            fail("Invalid session ID", "invalid_archive")
        sid = record["id"]
        engine, native_id = record.get("engine"), record.get("native_id") or ""
        if (sid in seen or engine not in cli.ENGINES or record.get("state") not in ("idle", "interrupted", "error", "paused", "completed")
                or (native_id and not cli.valid_native_id(native_id))
                or (native_id and (engine, native_id) in native_ids)):
            fail("Invalid or duplicate session identity", "invalid_archive")
        if record.get("cli_isolated") is not True:
            fail("Full native archives must use private homes", "invalid_archive")
        seen.add(sid)
        if native_id:
            native_ids.add((engine, native_id))
        sessions._model(record.get("model"), engine)
        record.pop("capability_mode", None)
        session_reasoning.effort(record.get("reasoning_effort", ""), engine)
        sessions._title(record.get("title"), required=False)
        if (not isinstance(record.get("messages"), list) or not isinstance(record.get("events"), list)
                or any(not isinstance(m, dict) or m.get("role") not in ("user", "assistant")
                       or not isinstance(m.get("content"), str) for m in record["messages"])
                or any(not isinstance(e, dict) for e in record["events"])):
            fail("Invalid session history", "invalid_archive")


def import_state(sessions, pid, source, manifest):
    store, source = sessions.store, Path(source).absolute()
    records = deepcopy(manifest.get("sessions"))
    _validate_records(sessions, records)
    engines, entries, old_root = manifest.get("engines"), manifest.get("entries"), manifest.get("source_root")
    if (manifest.get("kind") != KIND or manifest.get("version") != VERSION or manifest.get("native_complete") is not True
            or not isinstance(engines, dict) or set(engines) - set(cli.ENGINES)
            or not isinstance(entries, list) or len(entries) > MAX_ENTRIES
            or not isinstance(old_root, str) or not old_root or len(old_root) > 4096):
        fail("Invalid native archive manifest", "invalid_archive")
    for record in records:
        if record["engine"] not in engines and (record.get("native_started") or record.get("resumed") or record.get("native")):
            fail("Native engine state is missing", "invalid_archive")
    expected = {}
    for entry in entries:
        if not isinstance(entry, dict):
            fail("Invalid native archive entry", "invalid_archive")
        name = files.relative(entry.get("path"))
        parts = name.split("/")
        if len(parts) < 1 or parts[0] != "native" or (len(parts) > 1 and parts[1] not in engines):
            fail("Invalid native archive path", "invalid_archive")
        if len(parts) > 2:
            kind = _kind(parts[1], "/".join(parts[2:]))
            if kind == "excluded" or (kind == "sqlite" and parts[2] not in SQLITE_TABLES):
                fail("Archive contains excluded host/runtime state", "invalid_archive")
        if name in expected or type(entry.get("mode")) is not int or not 0 <= entry["mode"] <= 0o777:
            fail("Duplicate path or invalid file mode", "invalid_archive")
        if entry.get("kind") not in ("file", "directory"):
            fail("Invalid native entry type", "invalid_archive")
        expected[name] = entry
    actual = {entry["path"]: entry for entry in _inventory(source) if entry["path"] != "manifest.json"}
    if actual != expected:
        fail("Native archive inventory or checksum mismatch", "invalid_archive")
    root, meta = Path(store.root(pid)), Path(store.meta(pid))
    native_root = meta / "sessions" / "native"
    with store.lock(pid):
        data = sessions._load(pid)
        if sessions.busy(pid) or data["sessions"] or (native_root.exists() and any(native_root.iterdir())) or native_root.is_symlink():
            fail("Full native import requires a new empty project", "conflict")
        if str(root.absolute()) == old_root:
            fail("Full native import requires a new project identity", "conflict")
        with tempfile.TemporaryDirectory(prefix="easyread-native-import-", dir=meta) as tmp:
            stage = Path(tmp).resolve()
            for name, entry in expected.items():
                dest = stage / name
                if entry["kind"] == "directory":
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    _copy_regular(source / name, dest, [0])
            native_count = 0
            for engine, details in engines.items():
                if (not isinstance(details, dict) or details.get("adapter") != ADAPTERS[engine]
                        or not isinstance(details.get("source_home"), str) or not details["source_home"]
                        or len(details["source_home"]) > 4096 or not isinstance(details.get("sqlite_schemas"), dict)):
                    fail("Unsupported native archive adapter", "invalid_archive")
                home = stage / "native" / engine
                old_home = details["source_home"]
                text, structured = _relocator(old_root, root, old_home, cli.project_home(meta, engine))
                if sorted(_inspect_home(engine, home, old_root)) != details.get("native_ids"):
                    fail("Native session inventory mismatch", "invalid_archive")
                schemas = {}
                for path in home.glob("*.sqlite"):
                    with _database(path, True) as db:
                        schemas[path.name] = _schema(db, path.name)
                if schemas != details["sqlite_schemas"]:
                    fail("Native SQLite schema inventory mismatch", "invalid_archive")
                offsets = {}
                for path, info in list(_walk(home)):
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    relative = path.relative_to(home).as_posix()
                    format = _format(engine, relative)
                    if format == "sqlite":
                        continue
                    native_id = None
                    if engine == "codex" and path.name.startswith("rollout-") and path.suffix == ".jsonl":
                        native_id = _transcript_identity(engine, path, old_root)
                    mapping = _retarget_file(path, format, structured)
                    if native_id:
                        offsets[native_id] = mapping
                for path in home.glob("*.sqlite"):
                    _retarget_database(path, old_root, text, structured, offsets)
                if engine == "claude" and (home / "projects" / cli.claude_slug(old_root)).exists():
                    (home / "projects" / cli.claude_slug(old_root)).rename(home / "projects" / cli.claude_slug(root))
                _inspect_home(engine, home, root)
                for record in records:
                    if record["engine"] != engine:
                        continue
                    native_id = record.get("native_id") or ""
                    path = cli.transcript(engine, home, native_id, root) if native_id else None
                    if not path and (record.get("native_started") or record.get("resumed") or record.get("native")):
                        fail("Native session transcript is missing", "invalid_archive")
                    native_count += bool(path)
                    record.update(native_started=bool(path), cli_isolated=True, native_complete=True, imported=True)
                    record.pop("native", None)
                    record.pop("terminal_tracking", None)
            for name, entry in sorted(expected.items(), key=lambda item: len(item[0].split("/")), reverse=True):
                relocated = name
                if name.startswith("native/claude/projects/"):
                    relocated = name.replace("native/claude/projects/" + cli.claude_slug(old_root),
                                             "native/claude/projects/" + cli.claude_slug(root), 1)
                (stage / relocated).chmod(entry["mode"])
            published = False
            try:
                if (stage / "native").exists():
                    if native_root.exists():
                        native_root.rmdir()
                    os.replace(stage / "native", native_root)
                    published = True
                for record in records:
                    expire(record, "Imported native state requires explicit reconciliation before resuming")
                    data["sessions"][record["id"]] = record
                    data["order"].append(record["id"])
                sessions._write(pid, data)
            except Exception:
                if published:
                    shutil.rmtree(native_root)
                raise
    return {"sessions": len(records), "native": native_count, "native_complete": True}

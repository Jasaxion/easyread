"""Full private-native v2 archives; fixtures include hidden state and WAL databases."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from easyread.workspaces import session_archive as archive
from easyread.workspaces import session_cli as cli
from easyread.workspaces.sessions import Sessions, SessionError, ARCHIVE_KIND
from easyread.workspaces.store import Store
from tests.test_workspace_sessions import transcript, FakeTerminals


class WorkspaceSessionArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="easyread-native-archive-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"CODEX_HOME": str(self.base / "host-codex"),
            "CLAUDE_CONFIG_DIR": str(self.base / "host-claude"), "EASYREAD_WORKSPACE_CLI_ISOLATION": "1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = Store(self.base / "library")
        self.pid = self.store.allocate({"name": "Source"})["id"]
        self.root = self.store.root(self.pid)
        self.sessions = Sessions(self.store)
        self.sessions.terminals = FakeTerminals()
        self.addCleanup(self.sessions.close)
        self.dest = self.base / "bundle"

    def seed(self, engine):
        record = self.sessions.create(self.pid, {"engine": engine})["session"]
        native_id = record.get("native_id") or cli.new_native_id(engine)
        home = cli.project_home(self.store.meta(self.pid), engine)
        path = cli.transcript_target(engine, home, native_id, self.root)
        cli.write_transcript(path, transcript(engine, native_id, self.root))
        def update(data):
            data["sessions"][record["id"]].update(native_id=native_id, native_started=True,
                messages=[{"role": "user", "content": "history remains verbatim " + str(self.root)}],
                events=[{"kind": "hidden-detail", "text": "x" * 5000}],
                state="interrupted", error="old notice", custom_record={"opaque": [1, 2, 3]})
        self.sessions._commit(self.pid, update)
        return record["id"], native_id, home, path

    def put(self, path, content, mode=0o640):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        path.chmod(mode)

    def restore(self):
        pid = self.store.allocate({"name": "Imported"})["id"]
        result = self.sessions.import_state(pid, self.dest)
        return pid, result

    def db(self, home, name, schema, values=(), wal=False):
        path = home / name
        db = sqlite3.connect(path)
        if wal:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
        db.executescript(schema)
        for sql, params in values:
            db.execute(sql, params)
        db.commit()
        if wal:
            self.addCleanup(db.close)
        else:
            db.close()
        return path

    def test_codex_all_durable_databases_subagents_tasks_and_offset_indexes(self):
        sid, native_id, home, main = self.seed("codex")
        child_id = cli.uuid7()
        child = cli.transcript_target("codex", home, child_id, self.root)
        records = [json.loads(line) for line in transcript("codex", child_id, self.root).splitlines()]
        records[0]["payload"]["source"] = {"subagent": {"parent_thread_id": native_id}}
        cli.write_transcript(child, "".join(json.dumps(r) + "\n" for r in records))
        main_size = main.stat().st_size
        self.put(home / "tasks" / "task-1.json", json.dumps({"thread_id": child_id, "cwd": str(self.root),
                 "file": str(self.root / "draft.tex"), "blockedBy": ["task-0"]}))
        self.put(home / "subagents" / child_id / "opaque.bin", b"\x00\xffchild-state")
        self.put(home / "memories" / "MEMORY.md", "Use " + str(self.root / "draft.tex"))
        (home / "tasks" / "empty").mkdir(mode=0o750)
        self.db(home, "state_5.sqlite", """
            CREATE TABLE threads(id TEXT PRIMARY KEY,cwd TEXT,rollout_path TEXT,title TEXT);
            CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT,status TEXT);
            CREATE TABLE guardian_review_feedback(id TEXT,thread_id TEXT,record BLOB);
        """, [("INSERT INTO threads VALUES(?,?,?,?)", (native_id, str(self.root), str(main), "parent")),
              ("INSERT INTO threads VALUES(?,?,?,?)", (child_id, str(self.root), str(child), "child")),
              ("INSERT INTO thread_spawn_edges VALUES(?,?,?)", (native_id, child_id, "done")),
              ("INSERT INTO guardian_review_feedback VALUES(?,?,?)", ("review", child_id, b"\x00opaque"))], wal=True)
        self.db(home, "goals_1.sqlite", "CREATE TABLE thread_goals(thread_id TEXT,objective TEXT,status TEXT);",
                [("INSERT INTO thread_goals VALUES(?,?,?)", (native_id, "Finish paper", "active"))])
        self.db(home, "memories_1.sqlite", "CREATE TABLE stage1_outputs(thread_id TEXT,raw_memory TEXT,rollout_summary TEXT);",
                [("INSERT INTO stage1_outputs VALUES(?,?,?)", (child_id, "Memory " + str(self.root), "summary"))])
        self.db(home, "queue_1.sqlite", "CREATE TABLE queued_items(id TEXT,thread_id TEXT,payload_json TEXT);",
                [("INSERT INTO queued_items VALUES(?,?,?)", ("q1", native_id, json.dumps({"cwd": str(self.root), "task": "t1"})))])
        self.db(home, "thread_history_1.sqlite", """
            CREATE TABLE thread_history_projection_state(thread_id TEXT,next_rollout_byte_offset INTEGER);
            CREATE TABLE thread_turns(thread_id TEXT,rollout_byte_offset INTEGER,rollout_end_byte_offset INTEGER);
            CREATE TABLE thread_items(thread_id TEXT,item_json TEXT);
        """, [("INSERT INTO thread_history_projection_state VALUES(?,?)", (native_id, main_size)),
              ("INSERT INTO thread_turns VALUES(?,?,?)", (native_id, 0, main_size)),
              ("INSERT INTO thread_items VALUES(?,?)", (native_id, json.dumps({"cwd": str(self.root)})))])
        original_history = self.sessions.get(self.pid, sid)["session"]
        result = self.sessions.export_state(self.pid, self.dest)
        self.assertTrue(result["native_complete"])
        manifest = json.loads((self.dest / "manifest.json").read_text())
        self.assertEqual(set(manifest["engines"]["codex"]["sqlite_schemas"]), set(archive.SQLITE_TABLES))
        self.assertFalse(list((self.dest / "native/codex").glob("*-wal")))
        self.assertFalse(list((self.dest / "native/codex").glob("*-shm")))
        pid, result = self.restore()
        restored = self.sessions.get(pid, sid)["session"]
        new_home = cli.project_home(self.store.meta(pid), "codex")
        new_root = self.store.root(pid)
        self.assertEqual(restored["native_id"], native_id)
        for field in ("messages", "events", "custom_record", "state", "error", "created"):
            self.assertEqual(restored[field], original_history[field])
        self.assertEqual((new_home / "subagents" / child_id / "opaque.bin").read_bytes(), b"\x00\xffchild-state")
        self.assertEqual(stat.S_IMODE((new_home / "tasks/empty").stat().st_mode), 0o750)
        self.assertEqual(stat.S_IMODE((new_home / "tasks/task-1.json").stat().st_mode), 0o640)
        self.assertIn(str(new_root), (new_home / "memories/MEMORY.md").read_text())
        task = json.loads((new_home / "tasks/task-1.json").read_text())
        self.assertEqual(task["cwd"], str(new_root))
        self.assertEqual(task["thread_id"], child_id)
        with sqlite3.connect(new_home / "state_5.sqlite") as db:
            rows = db.execute("SELECT id,cwd,rollout_path FROM threads").fetchall()
            self.assertEqual({row[0] for row in rows}, {native_id, child_id})
            self.assertTrue(all(row[1] == str(new_root) and row[2].startswith(str(new_home)) for row in rows))
            self.assertEqual(db.execute("SELECT record FROM guardian_review_feedback").fetchone()[0], b"\x00opaque")
        new_main = cli.transcript("codex", new_home, native_id, new_root)
        with sqlite3.connect(new_home / "thread_history_1.sqlite") as db:
            self.assertEqual(db.execute("SELECT next_rollout_byte_offset FROM thread_history_projection_state").fetchone()[0], new_main.stat().st_size)
            self.assertEqual(db.execute("SELECT rollout_end_byte_offset FROM thread_turns").fetchone()[0], new_main.stat().st_size)
        with sqlite3.connect(new_home / "goals_1.sqlite") as db:
            self.assertEqual(db.execute("SELECT objective FROM thread_goals").fetchone()[0], "Finish paper")
        with sqlite3.connect(new_home / "queue_1.sqlite") as db:
            payload = json.loads(db.execute("SELECT payload_json FROM queued_items").fetchone()[0])
            self.assertEqual(payload["cwd"], str(new_root))

    def test_claude_subagent_task_file_history_opaque_modes_and_slug(self):
        sid, native_id, home, main = self.seed("claude")
        project = main.parent
        self.put(project / native_id / "subagents/agent-child.jsonl", transcript("claude", native_id, self.root))
        self.put(project / native_id / "tasks/hidden.json", json.dumps({"sessionId": native_id, "cwd": str(self.root)}))
        binary = b"{not-json}\x00\xff original version"
        self.put(home / "file-history" / native_id / "original.json", binary)
        self.put(project / native_id / "file-history/backup.json", binary)
        self.put(home / "tasks" / native_id / "1.json", json.dumps({"blockedBy": ["2"], "path": str(self.root / "paper.tex")}))
        self.put(home / "todos" / (native_id + ".json"), '[{"text":"unfinished","status":"pending"}]')
        self.put(project / "memory/MEMORY.md", "Root " + str(self.root))
        self.sessions.export_state(self.pid, self.dest)
        with patch.object(cli, "home", side_effect=AssertionError("import must not prepare auth")), patch.object(cli.subprocess, "Popen", side_effect=AssertionError("no execution on import")), patch.object(cli.subprocess, "run", side_effect=AssertionError("no execution on import")):
            pid, result = self.restore()
        self.assertTrue(result["native_complete"])
        new_home = cli.project_home(self.store.meta(pid), "claude")
        new_project = new_home / "projects" / cli.claude_slug(self.store.root(pid))
        self.assertFalse((new_home / "projects" / cli.claude_slug(self.root)).exists())
        self.assertEqual((new_home / "file-history" / native_id / "original.json").read_bytes(), binary)
        self.assertEqual((new_project / native_id / "file-history/backup.json").read_bytes(), binary)
        for path in (new_project / native_id / "subagents/agent-child.jsonl", new_project / native_id / "tasks/hidden.json"):
            self.assertIn(str(self.store.root(pid)), path.read_text())
            self.assertIn(native_id, path.read_text())
        self.assertEqual(self.sessions.get(pid, sid)["session"]["native_id"], native_id)

    def test_excludes_replaced_inheritance_auth_backups_env_logs_and_host_skills(self):
        for engine in cli.ENGINES:
            _, _, home, _ = self.seed(engine)
            for name in cli.SPECS[engine]["links"]:
                self.put(home / name, b"HOST-CREDENTIAL-SENTINEL")
            self.put(home / ".inheritance.json", b"HOST-CREDENTIAL-SENTINEL")
            if engine == "claude":
                self.put(home / "backups/.claude.json.backup.1", b"HOST-CREDENTIAL-SENTINEL")
                self.put(home / "shell-snapshots/env.sh", b"HOST-CREDENTIAL-SENTINEL")
            else:
                self.put(home / "logs_2.sqlite", b"HOST-CREDENTIAL-SENTINEL")
                self.put(home / "shell_snapshots/env.sh", b"HOST-CREDENTIAL-SENTINEL")
            host = cli.host_home(engine)
            host.mkdir()
            self.put(host / "private-auth", b"HOST-CREDENTIAL-SENTINEL")
            before = {p: p.read_bytes() for p in host.iterdir()}
            with patch.object(cli, "home", side_effect=AssertionError("export must not prepare auth")):
                if self.dest.exists(): shutil.rmtree(self.dest)
                self.sessions.export_state(self.pid, self.dest)
            self.assertEqual(before, {p: p.read_bytes() for p in host.iterdir()})
        for path in self.dest.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"HOST-CREDENTIAL-SENTINEL", path.read_bytes())
        manifest = json.loads((self.dest / "manifest.json").read_text())
        self.assertTrue(manifest["exclusions"])
        self.assertIn("auth.json", manifest["engines"]["codex"]["excluded_entries"])

    def test_inherited_symlinks_excluded_but_durable_symlinks_rejected(self):
        _, _, home, _ = self.seed("codex")
        secret = self.base / "host-secret"
        secret.write_text("NEVER EXPORT")
        (home / "auth.json").symlink_to(secret)
        self.sessions.export_state(self.pid, self.dest)
        self.assertFalse((self.dest / "native/codex/auth.json").exists())
        shutil.rmtree(self.dest)
        (home / "tasks").mkdir()
        (home / "tasks/escape").symlink_to(secret)
        with self.assertRaises(SessionError):
            self.sessions.export_state(self.pid, self.dest)

    def test_hardlinks_and_special_files_rejected(self):
        _, _, home, _ = self.seed("claude")
        (home / "tasks").mkdir()
        original = self.base / "original"
        original.write_bytes(b"private")
        target = home / "tasks/linked"
        os.link(original, target)
        with self.assertRaises(ValueError):
            self.sessions.export_state(self.pid, self.dest)
        target.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(target)
            with self.assertRaises(ValueError):
                self.sessions.export_state(self.pid, self.dest)

    def test_global_home_full_export_fails_clearly(self):
        self.seed("codex")
        self.sessions._commit(self.pid, lambda data: next(iter(data["sessions"].values())).update(cli_isolated=False))
        with self.assertRaises(SessionError) as raised:
            self.sessions.export_state(self.pid, self.dest)
        self.assertEqual(raised.exception.code, "native_isolation_required")

    def test_unknown_layout_database_schema_and_nonproject_rows_fail(self):
        _, native_id, home, _ = self.seed("codex")
        unknown = home / "new-format-v99"
        unknown.mkdir()
        with self.assertRaises(SessionError): self.sessions.export_state(self.pid, self.dest)
        unknown.rmdir()
        path = self.db(home, "state_5.sqlite", "CREATE TABLE unknown_future_state(data TEXT);")
        with self.assertRaises(SessionError): self.sessions.export_state(self.pid, self.dest)
        path.unlink()
        self.db(home, "state_5.sqlite", "CREATE TABLE threads(id TEXT,cwd TEXT);",
                [("INSERT INTO threads VALUES(?,?)", (native_id, str(self.base / "other-project")))])
        with self.assertRaises(SessionError): self.sessions.export_state(self.pid, self.dest)

    def test_other_project_transcripts_rejected(self):
        _, _, home, _ = self.seed("codex")
        other = cli.uuid7()
        cli.write_transcript(cli.transcript_target("codex", home, other, self.base), transcript("codex", other, self.base))
        with self.assertRaises(SessionError): self.sessions.export_state(self.pid, self.dest)

    def test_sqlite_host_rows_and_imported_trigger_code_rejected(self):
        _, _, home, _ = self.seed("codex")
        path = self.db(home, "state_5.sqlite", "CREATE TABLE remote_control_enrollments(account_id TEXT);",
                       [("INSERT INTO remote_control_enrollments VALUES(?)", ("private-host-identity",))])
        with self.assertRaises(SessionError): self.sessions.export_state(self.pid, self.dest)
        path.unlink()
        self.db(home, "state_5.sqlite", "CREATE TABLE threads(id TEXT,cwd TEXT); CREATE TRIGGER arbitrary AFTER UPDATE ON threads BEGIN SELECT load_extension('evil'); END;")
        with self.assertRaises(SessionError): self.sessions.export_state(self.pid, self.dest)

    def test_known_queue_triggers_preserved_without_revision_side_effects(self):
        _, native_id, home, _ = self.seed("codex")
        schema = "CREATE TABLE queued_items(id TEXT,thread_id TEXT,payload_json TEXT); CREATE TABLE queued_thread_revisions(revision INTEGER PRIMARY KEY AUTOINCREMENT,thread_id TEXT UNIQUE);"
        schema += archive.SQLITE_TRIGGERS["queued_items_revision_after_update"] + ";"
        self.db(home, "queue_1.sqlite", schema,
                [("INSERT INTO queued_items VALUES(?,?,?)", ("q", native_id, json.dumps({"cwd": str(self.root)}))),
                 ("INSERT INTO queued_thread_revisions VALUES(?,?)", (12, native_id))])
        self.sessions.export_state(self.pid, self.dest)
        pid, _ = self.restore()
        home = cli.project_home(self.store.meta(pid), "codex")
        with sqlite3.connect(home / "queue_1.sqlite") as db:
            self.assertEqual(db.execute("SELECT revision FROM queued_thread_revisions").fetchone()[0], 12)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0], 1)

    def test_legacy_v1_reader_still_changes_native_uuid(self):
        _, native_id, _, path = self.seed("codex")
        self.dest.mkdir()
        native = "native/codex/abcdef123456.jsonl"
        self.put(self.dest / native, path.read_bytes())
        manifest = {"kind": ARCHIVE_KIND, "version": 1, "sessions": [{"id": "abcdef123456", "engine": "codex",
                     "native_id": native_id, "native": native, "messages": [{"role": "user", "content": "legacy"}], "events": []}]}
        (self.dest / "manifest.json").write_text(json.dumps(manifest))
        pid, result = self.restore()
        record = next(iter(self.sessions._load(pid)["sessions"].values()))
        self.assertNotEqual(record["native_id"], native_id)
        self.assertFalse(result["native_complete"])
        self.assertIsNotNone(cli.transcript("codex", self.sessions._native_home(pid, record), record["native_id"], self.store.root(pid)))

    def test_inventory_tampering_unknown_adapter_and_excluded_paths_rejected(self):
        self.seed("claude")
        self.sessions.export_state(self.pid, self.dest)
        manifest_path = self.dest / "manifest.json"
        original = manifest_path.read_text()
        for change in (lambda m: m["engines"]["claude"].update(adapter="future-99"),
                       lambda m: m.update(native_complete=False),
                       lambda m: m["entries"][-1].update(sha256="bad"),
                       lambda m: m["entries"][-1].update(path="../escape")):
            value = json.loads(original)
            change(value)
            manifest_path.write_text(json.dumps(value))
            with self.assertRaises(ValueError): self.restore()
        manifest_path.write_text(original)
        self.put(self.dest / "native/claude/.credentials.json", b"hidden")
        with self.assertRaises(ValueError): self.restore()

    def test_v2_requires_empty_new_project_and_rolls_back_failed_publish(self):
        self.seed("codex")
        self.sessions.export_state(self.pid, self.dest)
        with self.assertRaises(SessionError): self.sessions.import_state(self.pid, self.dest)
        pid = self.store.allocate({"name": "Target"})["id"]
        with patch.object(self.sessions, "_write", side_effect=OSError("full disk")):
            with self.assertRaises(OSError): self.sessions.import_state(pid, self.dest)
        self.assertFalse((self.store.meta(pid) / "sessions/native").exists())
        self.assertFalse(self.sessions._load(pid)["sessions"])

    def test_unrelocatable_sqlite_field_fails_before_complete_export(self):
        _, native_id, home, _ = self.seed("codex")
        self.db(home, "state_5.sqlite", "CREATE TABLE threads(id TEXT,cwd TEXT,future_path_field TEXT);",
                [("INSERT INTO threads VALUES(?,?,?)", (native_id, str(self.root), str(self.root / "unknown")))])
        with self.assertRaises(SessionError) as raised:
            self.sessions.export_state(self.pid, self.dest)
        self.assertIn("Unsupported path-bearing", str(raised.exception))
        self.assertFalse((self.dest / "manifest.json").exists())

    def test_signed_reasoning_and_cross_platform_path_relocation(self):
        text, structured = archive._relocator(r"C:\research\old", self.base / "new",
                                              r"C:\native\old", self.base / "native-new")
        signed = {"type": "thinking", "thinking": r"Inspect C:\research\old", "signature": "opaque-signature"}
        self.assertEqual(structured(signed), signed)
        self.assertEqual(text("C:/research/old/paper.tex"), str(self.base / "new") + "/paper.tex")
        self.assertEqual(structured({"path": r"C:\native\old\task.json"})["path"], str(self.base / "native-new/task.json"))
        self.assertEqual(text("C:/research/old-neighbor/paper.tex"), "C:/research/old-neighbor/paper.tex")

    def test_source_database_and_wal_bytes_are_never_mutated_by_export(self):
        _, native_id, home, main = self.seed("codex")
        self.db(home, "state_5.sqlite", "CREATE TABLE threads(id TEXT,cwd TEXT,rollout_path TEXT);",
                [("INSERT INTO threads VALUES(?,?,?)", (native_id, str(self.root), str(main)))], wal=True)
        before = {path.name: path.read_bytes() for path in home.glob("state_5.sqlite*")}
        self.assertIn("state_5.sqlite-wal", before)
        self.sessions.export_state(self.pid, self.dest)
        after = {path.name: path.read_bytes() for path in home.glob("state_5.sqlite*")}
        self.assertEqual(before, after)

    def test_missing_engine_and_native_identity_inventory_rejected(self):
        self.seed("claude")
        self.sessions.export_state(self.pid, self.dest)
        path = self.dest / "manifest.json"
        original = path.read_text()
        for change in (lambda m: m.update(engines={}),
                       lambda m: m["engines"]["claude"].update(native_ids=[])):
            value = json.loads(original)
            change(value)
            path.write_text(json.dumps(value))
            with self.assertRaises(SessionError): self.restore()

    def test_malformed_auxiliary_json_fails_before_complete_export(self):
        _, _, home, _ = self.seed("codex")
        self.put(home / "tasks/bad.json", "{broken")
        with self.assertRaises(SessionError) as raised:
            self.sessions.export_state(self.pid, self.dest)
        self.assertEqual(raised.exception.code, "invalid_archive")
        self.assertFalse((self.dest / "manifest.json").exists())

    def test_nested_tool_result_jsonl_is_preserved_as_opaque_data(self):
        _, native_id, home, main = self.seed("claude")
        suffix = Path(native_id) / "tool-results/log.jsonl"
        self.put(main.parent / suffix, b"not native JSONL\x00\xff")
        self.sessions.export_state(self.pid, self.dest)
        pid, _ = self.restore()
        restored = cli.project_home(self.store.meta(pid), "claude") / "projects" / cli.claude_slug(self.store.root(pid))
        self.assertEqual((restored / suffix).read_bytes(), b"not native JSONL\x00\xff")

    def test_parent_project_zip_roundtrip_preserves_native_bundle(self):
        from easyread.workspaces import archives
        sid, native_id, home, _ = self.seed("claude")
        self.put(home / "file-history" / native_id / "previous.txt", b"old contents", mode=0o600)
        self.put(self.root / "paper.tex", b"workspace contents")
        raw = archives.export_project(self.store, self.sessions, self.pid)
        with patch.object(cli, "home", side_effect=AssertionError("no auth on import")):
            result = archives.import_project(self.store, self.sessions, raw)
        pid = result["project"]["id"]
        restored = self.sessions.get(pid, sid)["session"]
        self.assertEqual(restored["native_id"], native_id)
        self.assertTrue(restored["native_complete"])
        history = cli.project_home(self.store.meta(pid), "claude") / "file-history" / native_id / "previous.txt"
        self.assertEqual(history.read_bytes(), b"old contents")
        self.assertEqual(stat.S_IMODE(history.stat().st_mode), 0o600)
        self.assertEqual((self.store.root(pid) / "paper.tex").read_bytes(), b"workspace contents")

    def test_corrupt_sqlite_is_a_clear_archive_error(self):
        _, _, home, _ = self.seed("codex")
        self.put(home / "state_5.sqlite", b"not a database")
        with self.assertRaises(SessionError) as raised:
            self.sessions.export_state(self.pid, self.dest)
        self.assertEqual(raised.exception.code, "invalid_archive")
        self.assertFalse((self.dest / "manifest.json").exists())

    def test_deleted_rollout_orphan_indexes_survive_without_restoring_deleted_ui(self):
        deleted_sid, deleted_id, home, deleted_path = self.seed("codex")
        remaining_sid, remaining_id, _, remaining_path = self.seed("codex")
        deleted_size, remaining_size = deleted_path.stat().st_size, remaining_path.stat().st_size
        self.db(home, "state_5.sqlite", "CREATE TABLE threads(id TEXT,cwd TEXT,rollout_path TEXT);",
                [("INSERT INTO threads VALUES(?,?,?)", (deleted_id, str(self.root), str(deleted_path))),
                 ("INSERT INTO threads VALUES(?,?,?)", (remaining_id, str(self.root), str(remaining_path)))])
        self.db(home, "thread_history_1.sqlite", "CREATE TABLE thread_history_projection_state(thread_id TEXT,next_rollout_byte_offset INTEGER);",
                [("INSERT INTO thread_history_projection_state VALUES(?,?)", (deleted_id, deleted_size)),
                 ("INSERT INTO thread_history_projection_state VALUES(?,?)", (remaining_id, remaining_size))])
        self.sessions.delete(self.pid, deleted_sid)
        self.sessions.export_state(self.pid, self.dest)
        manifest = json.loads((self.dest / "manifest.json").read_text())
        diagnostics = manifest["engines"]["codex"]["diagnostics"]
        self.assertEqual(diagnostics, {"orphan_thread_ids": [deleted_id], "orphan_offset_fields": 1})
        self.assertIn("not secure erasure", manifest["deletion_note"])
        pid, result = self.restore()
        records = self.sessions._load(pid)["sessions"]
        self.assertEqual(set(records), {remaining_sid})
        self.assertNotIn(deleted_sid, records)
        restored_home = cli.project_home(self.store.meta(pid), "codex")
        path = cli.transcript("codex", restored_home, remaining_id, self.store.root(pid))
        self.assertIsNotNone(path)
        self.assertTrue(result["native_complete"])
        with sqlite3.connect(restored_home / "thread_history_1.sqlite") as db:
            rows = dict(db.execute("SELECT thread_id,next_rollout_byte_offset FROM thread_history_projection_state"))
        self.assertEqual(rows[deleted_id], deleted_size)
        self.assertEqual(rows[remaining_id], path.stat().st_size)

    def test_empty_session_full_archive_without_cli_install_or_auth(self):
        self.sessions.create(self.pid, {"engine": "claude"})
        with patch.object(cli, "version", side_effect=AssertionError("no CLI calls")), patch.object(cli, "home", side_effect=AssertionError("no auth")):
            result = self.sessions.export_state(self.pid, self.dest)
            _, imported = self.restore()
        self.assertTrue(result["native_complete"])
        self.assertTrue(imported["native_complete"])
        self.assertEqual(imported["native"], 0)


if __name__ == "__main__":
    unittest.main()

"""Indexed per-session journals and bounded history projections.

The legacy state.json is read once, transactionally imported, and left untouched.
Only explicit archive export materializes full history. A journal entry for a
message points at its current projection, so streaming an answer does not append
quadratically growing copies of that answer to disk.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import secrets
import sqlite3

from . import files, session_cli as cli

MESSAGES = 40
EVENTS = 200


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Records(dict):
    """Load history only for records a mutation actually touches."""

    def __init__(self, journal, summaries, full=False):
        super().__init__(summaries)
        self.journal, self.full = journal, full
        self.loaded = set()

    def __getitem__(self, sid):
        record = super().__getitem__(sid)
        if sid not in self.loaded and "messages" not in record:
            record.update(self.journal.history_record(sid, full=self.full))
            self.loaded.add(sid)
        return record

    def get(self, sid, default=None):
        return self[sid] if sid in self else default

    def values(self):
        return (self[sid] for sid in self)

    def items(self):
        return ((sid, self[sid]) for sid in self)

    def summaries(self):
        return dict.values(self)


class Journal:
    def __init__(self, directory):
        self.directory = directory
        self.path = directory / "journal.sqlite"

    @contextmanager
    def connect(self):
        # Metadata is outside the model's cwd, but still reject link substitution.
        with files.directory(self.directory):
            for path in (self.path, self.path.with_name(self.path.name + "-wal"),
                         self.path.with_name(self.path.name + "-shm")):
                if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
                    raise cli.SessionError("invalid_state", "Session journal must not be a link", 409)
            db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    sid TEXT PRIMARY KEY, position INTEGER NOT NULL,
                    summary TEXT NOT NULL, cursor INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    sid TEXT NOT NULL REFERENCES sessions(sid) ON DELETE CASCADE,
                    id TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(sid,id));
                CREATE INDEX IF NOT EXISTS messages_position ON messages(sid,position);
                CREATE TABLE IF NOT EXISTS journal (
                    sid TEXT NOT NULL REFERENCES sessions(sid) ON DELETE CASCADE,
                    seq INTEGER NOT NULL, kind TEXT NOT NULL, entity_id TEXT, payload TEXT,
                    PRIMARY KEY(sid,seq));
                CREATE INDEX IF NOT EXISTS journal_kind ON journal(sid,kind,seq);
            """)
            with db:
                yield db
        except sqlite3.DatabaseError as error:
            raise cli.SessionError("invalid_state", "Session journal is unreadable; history was not replaced", 409) from error
        finally:
            db.close()

    def initialize(self, legacy_loader):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM metadata WHERE key='initialized'").fetchone():
                return
            data = legacy_loader()
            self._save(db, data)
            db.execute("INSERT INTO metadata VALUES ('initialized','1')")

    def load(self, full=False):
        with self.connect() as db:
            rows = db.execute("SELECT sid,summary,cursor FROM sessions ORDER BY position").fetchall()
        records = {sid: {**json.loads(raw), "cursor": cursor} for sid, raw, cursor in rows}
        return {"version": 1, "seq": sum(r[2] for r in rows), "order": list(records),
                "sessions": Records(self, records, full)}

    def save(self, data):
        with self.connect() as db:
            self._save(db, data)

    def _save(self, db, data):
        known = {sid for (sid,) in db.execute("SELECT sid FROM sessions")}
        for sid in known - set(data["sessions"]):
            db.execute("DELETE FROM sessions WHERE sid=?", (sid,))
        entries = dict.items(data["sessions"])
        for sid, record in entries:
            row = db.execute("SELECT summary,cursor,position FROM sessions WHERE sid=?", (sid,)).fetchone()
            cursor = row[1] if row else 0
            previous = json.loads(row[0]) if row else None
            if row is None:
                db.execute("INSERT INTO sessions VALUES (?,?,?,0)", (sid, data["order"].index(sid), "{}"))

            def append(kind, payload=None, entity_id=None, preferred=None):
                nonlocal cursor
                seq = cursor + 1
                if (row is None and type(preferred) is int and 0 < preferred < 2**63 - 1
                        and not db.execute("SELECT 1 FROM journal WHERE sid=? AND seq=?", (sid, preferred)).fetchone()):
                    seq = preferred
                cursor = max(cursor, seq)
                db.execute("INSERT INTO journal VALUES (?,?,?,?,?)",
                           (sid, seq, kind, entity_id, encode(payload) if payload is not None else None))
                return seq

            for message in record.get("messages", []):
                message.setdefault("id", secrets.token_hex(12))
                old = db.execute("SELECT payload,position FROM messages WHERE sid=? AND id=?",
                                 (sid, message["id"])).fetchone()
                if old and json.loads(old[0]) == message:
                    continue
                seq = append("message", entity_id=message["id"], preferred=message.get("seq"))
                message["seq"] = old[1] if old else seq
                db.execute("INSERT INTO messages VALUES (?,?,?,?) ON CONFLICT(sid,id) DO UPDATE SET payload=excluded.payload",
                           (sid, message["id"], message["seq"], encode(message)))
            for event in record.get("events", []):
                if row is not None and event.get("seq") and db.execute("SELECT 1 FROM journal WHERE sid=? AND seq=? AND kind='event'",
                                                   (sid, event["seq"])).fetchone():
                    continue
                seq = append("event", event, preferred=event.get("seq"))
                event["seq"] = seq
                db.execute("UPDATE journal SET payload=? WHERE sid=? AND seq=?", (encode(event), sid, seq))
            summary = {k: v for k, v in record.items() if k not in
                       ("messages", "events", "cursor", "history_before", "has_more")}
            if summary != previous:
                # State/pending changes advance the same cursor even without output.
                append("state")
            position = data["order"].index(sid)
            if row is None or summary != previous or cursor != row[1] or position != row[2]:
                db.execute("UPDATE sessions SET summary=?,cursor=?,position=? WHERE sid=?",
                           (encode(summary), cursor, position, sid))
            record["cursor"] = cursor
        data["seq"] = sum(row[0] for row in db.execute("SELECT cursor FROM sessions"))

    def history_record(self, sid, full=False):
        if full:
            with self.connect() as db:
                messages = [json.loads(r[0]) for r in db.execute(
                    "SELECT payload FROM messages WHERE sid=? ORDER BY position", (sid,))]
                events = [json.loads(r[0]) for r in db.execute(
                    "SELECT payload FROM journal WHERE sid=? AND kind='event' ORDER BY seq", (sid,))]
            return {"messages": messages, "events": events}
        with self.connect() as db:
            messages = [json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM messages WHERE sid=? ORDER BY position DESC LIMIT ?", (sid, MESSAGES))]
            events = [json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM journal WHERE sid=? AND kind='event' ORDER BY seq DESC LIMIT ?", (sid, EVENTS))]
        return {"messages": list(reversed(messages)), "events": list(reversed(events))}

    def message(self, sid, mid):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM messages WHERE sid=? AND id=?", (sid, mid)).fetchone()
        return json.loads(row[0]) if row else None

    def page(self, sid, before=None, limit=MESSAGES):
        limit = max(1, min(int(limit), MESSAGES))
        recent = before is None
        before = int(before) if before is not None else 2**63 - 1
        if not 0 <= before <= 2**63 - 1:
            raise cli.SessionError("invalid_parameter", "History cursor is out of range")
        with self.connect() as db:
            messages = db.execute("SELECT position,payload FROM messages WHERE sid=? AND position<? ORDER BY position DESC LIMIT ?",
                                  (sid, before, limit + 1)).fetchall()
            events = db.execute("SELECT seq,payload FROM journal WHERE sid=? AND kind='event' AND seq<? ORDER BY seq DESC LIMIT ?",
                                (sid, before, EVENTS + 1)).fetchall()
        # One common boundary ensures paging never skips messages or busy tool logs.
        cuts = []
        if len(messages) > limit:
            cuts.append(messages[limit - 1][0])
        if len(events) > EVENTS:
            cuts.append(events[EVENTS - 1][0])
        cutoff = max(cuts) if cuts else 0
        # The initial independent last slices may overlap an older page. Stable
        # message IDs/event seqs let clients dedupe without losing either stream.
        return {"messages": [json.loads(raw) for seq, raw in reversed(messages[:limit] if recent else messages) if recent or seq >= cutoff],
                "events": [json.loads(raw) for seq, raw in reversed(events[:EVENTS] if recent else events) if recent or seq >= cutoff],
                "history_before": cutoff or None, "has_more": bool(cutoff)}

    def updates(self, sid, after, limit=100):
        after, limit = int(after), max(1, min(int(limit), 200))
        if not 0 <= after <= 2**63 - 1:
            raise cli.SessionError("invalid_parameter", "Cursor is out of range")
        with self.connect() as db:
            row = db.execute("SELECT cursor FROM sessions WHERE sid=?", (sid,)).fetchone()
            if not row:
                raise cli.SessionError("not_found", "Session not found", 404)
            if after > row[0]:
                return {"messages": [], "events": [], "next_cursor": row[0], "has_more": False, "reset": True}
            rows = db.execute("SELECT seq,kind,entity_id,payload FROM journal WHERE sid=? AND seq>? ORDER BY seq LIMIT ?",
                              (sid, after, limit + 1)).fetchall()
            messages = {}
            events = []
            for seq, kind, entity_id, raw in rows[:limit]:
                if kind == "message":
                    message = db.execute("SELECT payload FROM messages WHERE sid=? AND id=?", (sid, entity_id)).fetchone()
                    if message:
                        messages[entity_id] = json.loads(message[0])
                elif kind == "event":
                    events.append(json.loads(raw))
        return {"messages": list(messages.values()), "events": events,
                "next_cursor": rows[min(limit, len(rows)) - 1][0] if rows else after,
                "has_more": len(rows) > limit}


def expire(record, message):
    """A persisted request is evidence, never a live handle after restart/import."""
    record["pending_requests"] = [dict(request, expired=True) for request in record.get("pending_requests", [])]
    was_native_active = record.get("native_active")
    record.update(runtime_state="sleeping", active_turn_id=None, native_active=False)
    if (record.get("state") in ("queued", "running", "waiting_user", "waiting_external")
            or was_native_active or record.get("background_active") or record.get("queued")
            or (record.get("goal") or {}).get("status") in ("active", "blocked", "usageLimited", "budgetLimited")):
        record.update(state="interrupted", error=message, reconciliation_required=True)

"""Pi intent receipts and occurrence-based native queue projection.

Native queues contain expanded text, not client IDs. Only one preflight may be
outstanding; duplicate occurrences retain FIFO identity. Expanded text stays in
memory, never in public queue metadata. Nothing here replays an intent.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from copy import deepcopy
import re

from . import pi_sessions, session_cli as cli

PENDING = {"submitting", "accepted", "queued", "unknown"}
TERMINAL = {"delivered", "handled", "failed", "cleared"}
MAX_INPUTS = 64


class Inputs:
    def __init__(self):
        self.items = {}
        self.queues = {"steering": [], "followUp": []}
        self.removed = deque()
        self.inflight = None
        self.clearing = False

    def add(self, item):
        # Never evict live uncertain or pending intent. Explicitly retired
        # receipts can leave this projection only after their journal is saved.
        for rid in list(self.items):
            if len(self.items) < MAX_INPUTS:
                break
            if self.items[rid]["status"] in TERMINAL or self.items[rid].get("retired"):
                del self.items[rid]
        if len(self.items) >= MAX_INPUTS:
            raise cli.SessionError("queue_capacity", "Pi input receipts are full; clear or reconcile pending work", 409)
        item["turn_id"] = item["id"]  # Public display association, not the native run/steering token.
        self.items[item["id"]] = item
        self.inflight = item["id"]

    def update_queue(self, event):
        added = []
        for key in self.queues:
            values = event.get(key)
            if not isinstance(values, list) or any(not isinstance(text, str) for text in values) or len(values) > 256:
                raise ValueError("Malformed Pi queue snapshot")
            previous = defaultdict(deque)
            for entry in self.queues[key]:
                previous[entry["text"]].append(entry)
            # Pi consumes the oldest identical occurrence. A shrinking count
            # therefore preserves the suffix, not the first matching receipt.
            counts = Counter(values)
            removed = []
            for text, entries in previous.items():
                while len(entries) > counts[text]:
                    removed.append(entries.popleft())
            current = []
            for text in values:
                if previous[text]:
                    entry = previous[text].popleft()
                else:
                    entry = {"text": text, "id": None}
                    added.append((key, entry))
                current.append(entry)
            for entry in removed:
                if self.clearing:
                    if entry["id"] in self.items:
                        self.items[entry["id"]]["status"] = "cleared"
                else:
                    self.removed.append(entry)
            self.queues[key] = current
        if len(added) == 1 and self.inflight:
            item = self.items[self.inflight]
            wanted = "followUp" if item["kind"] == "follow_up" else "steering"
            if added[0][0] == wanted:
                added[0][1]["id"] = self.inflight
                item["status"] = "queued"
        if len(self.removed) > 256:
            raise ValueError("Unreconciled Pi queue delivery exceeds the limit")

    def delivered(self, message):
        text = pi_sessions.text_content(message.get("content"))
        # Pi removes the occurrence before message_start and can append image
        # normalization hints to its text. Do not use text as a global ID.
        match = next((entry for entry in self.removed
                      if text == entry["text"] or text.startswith(entry["text"] + "\n\n")), None)
        rid = None
        if match:
            self.removed.remove(match)
            rid = match["id"]
        else:
            candidates = [item for item in self.items.values()
                          if item["status"] in ("submitting", "accepted") and item["kind"] in ("send", "follow_up")]
            if len(candidates) == 1:
                rid = candidates[0]["id"]
        if rid in self.items:
            self.items[rid]["status"] = "delivered"
            return rid
        return None

    def acknowledge(self, event):
        rid = event["id"]
        item = self.items.get(rid)
        if not item:
            return
        if self.inflight == rid:
            self.inflight = None
        if item["status"] == "unknown":
            # A late acknowledgement is evidence, never authorization to replay
            # or silently clear the reconciliation gate.
            item["late_disposition"] = (event.get("data") or {}).get("disposition", "")
            return
        if not event["success"]:
            item.update(status="failed", error=str(event.get("error") or "Pi rejected input")[:2000])
            return
        disposition = (event.get("data") or {}).get("disposition")
        if item["kind"] == "command":
            item["status"] = "handled"
        elif disposition not in ("started", "queued", "handled"):
            raise ValueError("Pi omitted the authoritative prompt disposition")
        elif item["status"] not in TERMINAL:
            item["status"] = {"started": "accepted", "queued": "queued", "handled": "handled"}[disposition]

    def project(self, record):
        record["pending_inputs"] = deepcopy(list(self.items.values()))
        record["pending_input_count"] = sum(item["status"] in PENDING and not item.get("retired") for item in self.items.values())
        record["native_queue"] = {"steering": len(self.queues["steering"]), "follow_up": len(self.queues["followUp"])}
        for message in record["messages"]:
            if message.get("id") in self.items:
                item = self.items[message["id"]]
                if item.get("turn_id"):
                    message["turn_id"] = item["turn_id"]
                message["delivery_status"] = item["status"]
                if item.get("error"):
                    message["delivery_error"] = item["error"]
                if item.get("retired"):
                    message["delivery_retired"] = True

    def interrupt(self, reason):
        for item in self.items.values():
            if item["status"] in PENDING and not item.get("retired"):
                item.update(status="unknown", error=reason)


def public_native_text(text):
    # Extension-injected Skill messages without a corresponding client receipt
    # are not a reason to leak a private Skill body into the chat projection.
    return re.sub(r'<skill\b[^>]*>[\s\S]*?</skill>', '[Native skill prompt]', text)

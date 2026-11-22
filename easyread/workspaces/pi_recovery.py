"""Confirmed Pi writer release and fail-closed unowned crash generations."""
from __future__ import annotations

from . import session_cli as cli
from .session_store import expire

UNOWNED = ("The previous Pi runtime generation has no owned process handle and its exit was not confirmed. "
           "An operator must reconcile the old EasyRead/Pi process and native session file before resuming; "
           "history and attachments remain readable. This service will not guess a PID or replay unknown input.")


def unowned(record):
    """Unreleased durable reservation; a caller with its live handle still owns it."""
    return (record.get("engine") == "pi" and record.get("generation")
            and record.get("runtime_released_generation") != record["generation"])


def finish_stop(sessions, pid, sid):
    def stopped(data):
        record = sessions._record(data, sid)
        if record["engine"] != "pi" or not record.get("generation") or record.get("native_sync_warning"):
            return
        if unowned(record):
            raise cli.SessionError("reconciliation_required", UNOWNED, 409)
        if record.get("runtime_released_generation") != record["generation"]:
            return
        expire(record, "Pi stopped; unresolved input was not replayed")
        for item in record.get("pending_inputs", []):
            if item.get("status") == "unknown" and not item.get("retired"):
                item["retired"] = True
                message = next((m for m in record["messages"] if m.get("id") == item["id"]), None)
                if message is None:
                    message = sessions._journal(pid).message(sid, item["id"])
                    if message:
                        record["messages"].append(message)
                if message:
                    message.update(delivery_status="unknown", delivery_retired=True, delivery_error=item.get("error", ""))
                else:
                    # Native controls have no user chat message. Keep their
                    # original intent/outcome in the journal before eviction.
                    record["events"].append({**item, "kind": "input_retired", "input_kind": item["kind"], "at": cli.now_iso()})
        record.update(reconciliation_required=False, queued=False, native_active=False,
                      submission_pending=False, pending_input_count=0, runtime_phase="interrupted", state="interrupted")
    sessions._commit(pid, stopped)

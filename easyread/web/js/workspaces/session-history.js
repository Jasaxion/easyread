/* Only the selected session has history in memory; summaries never become transcripts. */
(function (PR, W) {
  "use strict";
  const limits = { messages: 80, events: 400, olderMessages: 240, olderEvents: 1200 };
  const messageKey = message => message.id != null ? JSON.stringify(["id", message.id]) :
    JSON.stringify([message.turn_id || message.at || message.content, message.role]);
  const eventKey = event => event.seq != null ? "seq:" + event.seq : JSON.stringify(event);
  function merge(previous, incoming, key, limit, older = false) {
    const rows = new Map();
    (older ? [...incoming, ...previous] : [...previous, ...incoming]).forEach(item => rows.set(key(item), item));
    const items = [...rows.values()];
    return older ? items.slice(0, limit) : items.slice(-limit);
  }
  class SessionHistory {
    constructor(data) {
      this.session = { ...data.session, messages: (data.session.messages || []).slice(-limits.messages),
        events: (data.session.events || []).slice(-limits.events) };
      this.cursor = data.cursor;
      this.before = data.history_before;
      this.hasOlder = !!data.has_more;
      this.catchingUp = false;
      this.older = null;
    }
    update(data) {
      if (data.reset) return false;
      if (Number.isSafeInteger(data.next_cursor) && data.next_cursor < this.cursor) return true;
      if (this.session.messages.length + (data.messages?.length || 0) > limits.messages ||
        this.session.events.length + (data.events?.length || 0) > limits.events) this.hasOlder = true;
      this.session = { ...this.session, ...data.session,
        pending_requests: data.pending_requests || [],
        messages: merge(this.session.messages, data.messages || [], messageKey, limits.messages),
        events: merge(this.session.events, data.events || [], eventKey, limits.events) };
      this.cursor = data.next_cursor;
      this.catchingUp = !!data.has_more;
      return true;
    }
    snapshot(data) {
      if (data.cursor < this.cursor) return;
      this.update({ session: data.session, messages: data.session.messages, events: data.session.events,
        pending_requests: data.session.pending_requests, next_cursor: data.cursor });
    }
    prepend(data) {
      const previous = this.older || this.session;
      this.older = {
        messages: merge(previous.messages, data.messages || [], messageKey, limits.olderMessages, true),
        events: merge(previous.events, data.events || [], eventKey, limits.olderEvents, true)
      };
      // This is the server's journal boundary, not an index into either bounded cache.
      this.before = data.history_before;
      this.hasOlder = !!data.has_more;
    }
    get view() { return this.older ? { ...this.session, ...this.older, viewing_history: true } : this.session; }
  }
  W.SessionHistory = SessionHistory;
  W.sessionBusy = session => !!session && (!!session.terminal_open || !!session.active_turn_id || session.pending_input_count > 0 || !!session.submission_pending ||
    ["running", "queued", "waiting_user", "waiting_external"].includes(session.state) ||
    ["active", "blocked"].includes(session.goal?.status) ||
    (session.pending_requests || []).some(request => !request.expired && request.is_blocking));
  // Match session_controls.require_idle's observable work checks; the server also validates live runtimes.
  W.sessionOwnsWork = session => !!session && (!!session.terminal_open || !!session.active_turn_id ||
    !!session.native_active || !!session.background_active || !!session.queued ||
    ["running", "queued", "waiting_user", "waiting_external"].includes(session.state) ||
    session.runtime_state === "starting" || session.pending_count > 0 || session.pending_input_count > 0 || !!session.submission_pending ||
    ["active", "blocked", "usageLimited", "budgetLimited"].includes(session.goal?.status) ||
    (session.pending_requests || []).some(request => !request.expired));
  // Reconciliation flags can outlive cancellation: offer Stop without permanently blocking explicit retries.
  W.sessionCanStop = session => !!session && (W.sessionBusy(session) || !!session.native_active ||
    !!session.background_active || !!session.queued || !!session.reconciliation_required ||
    session.state === "interrupted" || session.pending_count > 0 ||
    (session.pending_requests || []).some(request => !request.expired));
  W.requestKey = request => JSON.stringify([request.id, request.generation]);
  W.pendingRequest = (session, request) => (session?.pending_requests || []).find(item =>
    W.requestKey(item) === W.requestKey(request) && item.thread_id === request.thread_id &&
    item.turn_id === request.turn_id && !item.expired);
})(window.PR, window.PR.workspaces);

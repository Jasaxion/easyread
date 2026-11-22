/* Native adapters already normalize CLI events. Present tools, not transport JSON. */
(function (PR, W) {
  "use strict";
  let expanded;
  function expansion() {
    if (!expanded) expanded = new Map(Object.entries(PR.ls.get(W.key("activity-expanded"), {})));
    return expanded;
  }
  const saveExpansion = PR.debounce(() => W.store("activity-expanded", Object.fromEntries([...expansion()].slice(-1000))), 250);
  // New records have stable turn IDs. Older archives use user timestamps/queued boundaries.
  W.conversationTurns = session => {
    const turns = [], ids = new Map(); let current = null;
    const make = key => { const turn = { key, messages: [], events: [] }; turns.push(turn); ids.set(key, turn); return turn; };
    (session?.messages || []).forEach((message, index) => {
      if (message.turn_id) current = ids.get(message.turn_id) || make(message.turn_id);
      else if (message.role === "user" || !current) current = make("legacy:" + (message.id ?? message.at ?? index));
      current.messages.push(message);
    });
    const users = turns.filter(turn => turn.messages.some(message => message.role === "user"));
    const time = value => typeof value === "number" ? value : Date.parse(value || "") || 0;
    const at = turn => time(turn.messages.find(message => message.role === "user")?.at);
    let queued = -1, legacy = null;
    (session?.events || []).forEach(event => {
      let turn;
      if (event.turn_id) {
        turn = ids.get(event.turn_id);
        // A native goal can start a tool-only turn before its first assistant message.
        if (!turn && ["tool", "tool_result", "thinking", "plan", "notice"].includes(event.kind)) turn = make(event.turn_id);
      }
      else {
        if (event.kind === "queued") {
          queued++;
          const stamp = time(event.at);
          legacy = stamp && users.some(at) ? users.reduce((best, item) => Math.abs(at(item) - stamp) < Math.abs(at(best) - stamp) ? item : best, users[0]) : users[Math.min(queued, users.length - 1)];
        }
        turn = legacy;
        if (!turn && event.at && users.some(at)) turn = users.filter(item => at(item) <= time(event.at)).at(-1) || users[0];
        turn ||= turns[0];
      }
      // An orphan transport record must not recreate a standalone activity list.
      if (turn) turn.events.push(event);
    });
    turns.forEach((turn, index) => {
      const display = Object.hasOwn(session || {}, "active_display_turn_id") ? session.active_display_turn_id : session?.active_turn_id;
      turn.running = display ? turn.key === display : !Object.hasOwn(session || {}, "active_display_turn_id") && !session?.viewing_history && index === turns.length - 1 && session?.state === "running";
      turn.state = ["submitting", "settling", "retrying", "compacting"].includes(session?.runtime_phase) ? session.runtime_phase : session?.state;
    });
    return turns;
  };
  const text = value => typeof value === "string" ? value : value == null ? "" : JSON.stringify(value, null, 2);
  function details(key, cls) {
    const node = PR.el("details", { class: cls, "data-activity-key": key }); node.open = expansion().get(key) || false;
    node.addEventListener("toggle", () => { if (node.isConnected) { expansion().set(key, node.open); saveExpansion(); } });
    return node;
  }
  function tools(round) {
    const rows = [], byId = new Map(); let turn = "history";
    (round.events || []).forEach((event, index) => {
      if (!event || typeof event !== "object") return;
      if (event.kind === "queued") { turn = event.at || String(index); byId.clear(); return; }
      if (event.kind === "tool" || event.kind === "tool_result") {
        const id = event.id || "event:" + (event.at || index);
        let row = byId.get(id);
        if (!row) {
          row = { key: turn + ":" + id, name: event.name || PR.t("工具"), detail: "", output: "", state: "running" };
          rows.push(row); byId.set(id, row);
        }
        if (event.name) row.name = event.name;
        if (event.detail) row.detail = text(event.detail);
        if (event.kind === "tool_result") { row.output = text(event.text); row.state = event.ok === false ? "error" : "done"; }
        return;
      }
      // Init, usage, text/final chunks duplicate the conversation and are deliberately omitted.
      if (["thinking", "plan", "notice"].includes(event.kind) || (event.kind === "result" && event.ok === false)) {
        const detail = text(event.text || event.detail); if (!detail) return;
        rows.push({ key: turn + ":" + event.kind + ":" + (event.at || index), name: event.kind === "thinking" ? PR.t("思考") : event.kind === "plan" ? PR.t("计划") : PR.t("运行提示"), detail,
          state: event.subtype === "error" || event.ok === false ? "error" : "done" });
      }
    });
    rows.forEach(row => { if (row.state === "running" && !round.running) row.state = "interrupted"; });
    return rows;
  }
  W.captureActivity = box => box.querySelectorAll("[data-activity-key]").forEach(node => expansion().set(node.dataset.activityKey, node.open));
  W.renderActivity = (box, session, round) => {
    if (!session || !round) return;
    const rows = tools(round); if (!rows.length) return;
    const group = details(session.id + ":" + round.key + ":activity", "ws-activity");
    const heading = PR.el("summary");
    const final = [...round.events].reverse().find(event => event.kind === "state");
    const succeeded = final?.state === "idle" || (!final && round.messages.some(message => message.role === "assistant" && !message.partial));
    const status = round.running ? (W.sessionStateLabel?.(round.state) || PR.t("运行中")) : succeeded ? PR.t("已完成") : final?.state === "error" ? PR.t("出错") : rows.some(row => row.state === "interrupted") ? PR.t("已中断") : PR.t("已完成");
    heading.append(PR.el("span", { text: PR.t("操作 · {n}", { n: rows.length }) }), PR.el("span", { class: "ws-activity-state", text: status }));
    group.append(heading);
    rows.forEach(row => {
      const card = details(session.id + ":" + round.key + ":" + row.key, "ws-activity-card");
      const summary = PR.el("summary"), icon = PR.el("span", { class: "ws-activity-icon" });
      const shell = /bash|shell|command|exec|terminal/i.test(row.name);
      if (shell) icon.textContent = ">_";
      else icon.innerHTML = PR.icon(/search|grep|glob|find|web/i.test(row.name) ? "search" : /read|write|edit|file|patch/i.test(row.name) ? "page" : "log", "sm");
      let hint = row.detail;
      try { const input = JSON.parse(hint); hint = input.file_path || input.path || input.command || input.pattern || input.query || hint; } catch (_) {}
      const label = PR.el("span", { class: "ws-activity-label", text: row.name + (hint ? " · " + text(hint).split("\n")[0].slice(0, 160) : "") });
      const state = PR.el("span", { class: "ws-activity-state " + row.state, text: ({ running: PR.t("运行中"), done: PR.t("已完成"), error: PR.t("出错"), interrupted: PR.t("已中断") })[row.state] });
      summary.append(icon, label, state); card.append(summary);
      if (row.detail) card.append(PR.el("pre", { text: row.detail }));
      if (row.output) card.append(PR.el("pre", { text: row.output }));
      group.append(card);
    });
    box.append(group);
  };
})(window.PR, window.PR.workspaces);

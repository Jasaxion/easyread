/* A project-scoped view over native Beads; no browser copy of the database. */
(function (PR, W) {
  "use strict";
  const B = W.beads = {};
  B.statuses = () => ({ open: PR.t("待处理"), in_progress: PR.t("进行中"), blocked: PR.t("受阻"), deferred: PR.t("已推迟"), closed: PR.t("已完成") });
  B.types = () => ({ task: PR.t("任务"), bug: PR.t("缺陷"), feature: PR.t("功能"), epic: PR.t("史诗"), chore: PR.t("维护") });
  B.lane = (issue, blocked) => issue.status !== "closed" && blocked.has(issue.id) ? "blocked" : Object.hasOwn(B.statuses(), issue.status) ? issue.status : "other";
  B.matches = (issue, filters, ready, blocked) => {
    const haystack = [issue.id, issue.title, issue.description, issue.assignee, ...(issue.labels || [])].join(" ").toLocaleLowerCase();
    return (!filters.q || haystack.includes(filters.q.toLocaleLowerCase())) &&
      (!filters.status || (filters.status === "ready" ? ready.has(issue.id) : B.lane(issue, blocked) === filters.status)) &&
      (!filters.type || issue.issue_type === filters.type) && (!filters.priority || String(issue.priority) === filters.priority);
  };
  B.select = (label, choices, value = "") => {
    const input = PR.el("select", { class: "input", "aria-label": label });
    Object.entries(choices).forEach(([key, text]) => input.append(PR.el("option", { value: key, text })));
    if (value && !Object.hasOwn(choices, value)) input.append(PR.el("option", { value, text: value }));
    input.value = value; return input;
  };
  B.fingerprint = issue => issue ? JSON.stringify([issue.updated_at, issue.title, issue.description || "", issue.status, issue.issue_type, issue.priority, issue.assignee || "", [...(issue.labels || [])].sort()]) : "";
  B.live = p => B.panel === p && p.dialog.open && W.project?.id === p.pid;
  B.error = (p, error) => { if (B.live(p)) { p.error.textContent = error?.message || ""; p.error.hidden = !error; } };
  B.canLeave = async p => {
    if (p.busy) return false;
    if (!p.dirty) return true;
    const ok = await PR.confirm({ title: PR.t("放弃未保存的任务修改？"), body: PR.t("任务表单或评论尚未保存。关闭或切换任务后，这些修改将丢失。"), danger: true, ok: PR.t("放弃修改") });
    return ok && B.live(p) && !p.busy;
  };
  B.close = async p => { if (await B.canLeave(p)) p.dialog.close(); };
  B.show = async (p, id) => {
    if (!await B.canLeave(p) || !B.live(p)) return;
    p.dirty = false; p.commentDraft = ""; p.selected = id; p.detailIssue = null; p.actionError = null; p.conflict = false;
    B.render(p); await B.loadDetail(p, id);
  };
  B.loadDetail = async (p, id) => {
    const seq = ++p.detailSeq;
    p.detail.hidden = false; p.body.classList.add("has-detail");
    p.detail.replaceChildren(W.empty(PR.t("正在载入…")));
    try {
      const data = id ? await p.request("/show?" + new URLSearchParams({ id })) : { issue: null, comments: [] };
      if (!B.live(p) || seq !== p.detailSeq) return;
      p.dirty = false; p.conflict = false; p.actionError = null; B.error(p, null);
      p.detailIssue = data.issue; B.renderDetail(p, data);
      const comment = p.detail.querySelector(".ws-beads-comments textarea");
      if (p.commentDraft && comment) { comment.value = p.commentDraft; p.commentDraft = ""; p.dirty = true; }
    } catch (error) {
      if (!B.live(p) || seq !== p.detailSeq) return;
      p.dirty = !!p.commentDraft;
      p.detail.replaceChildren(W.empty(error.message), W.button(PR.t("重试"), () => B.loadDetail(p, id), "sync"));
    }
  };
  B.mutate = async (p, action, body, after) => {
    if (!B.live(p) || p.busy) return;
    p.busy = true; p.listSeq++; p.dialog.setAttribute("aria-busy", "true");
    let saved = false;
    const controls = [...p.dialog.querySelectorAll("button, input, select, textarea, fieldset")];
    const disabled = controls.map(el => el.disabled); controls.forEach(el => { el.disabled = true; });
    p.actionError = null; B.error(p, null);
    try {
      const result = await p.request(action, body);
      if (!B.live(p)) return;
      saved = true; p.dirty = false; p.notice.textContent = PR.t("已保存到当前项目的 Beads");
      await after(result);
    } catch (error) {
      p.actionError = error;
      if (error.code === "beads_conflict") { p.conflict = true; if (p.stale) p.stale.hidden = false; }
      B.error(p, error);
    }
    finally {
      p.busy = false; p.dialog.removeAttribute("aria-busy");
      controls.forEach((el, index) => { el.disabled = disabled[index]; });
    }
    if (saved && B.live(p)) await B.refresh(p);
  };
  B.refresh = async p => {
    if (!B.live(p) || p.loading || p.busy) return;
    p.loading = true; p.refresh.disabled = true;
    const seq = ++p.listSeq;
    try {
      const data = await p.request("");
      if (!B.live(p) || seq !== p.listSeq) return;
      const signature = JSON.stringify(data), changed = p.signature !== signature;
      p.signature = signature; p.issues = data.issues || []; p.ready = new Set(data.ready_ids || []); p.blocked = new Set(data.blocked_ids || []); p.loaded = true;
      p.truncated.hidden = !data.truncated; p.create.disabled = false;
      B.error(p, p.actionError); if (changed) B.render(p);
      const current = p.issues.find(issue => issue.id === p.selected);
      if (p.stale) p.stale.hidden = !p.conflict && (!p.detailIssue || B.fingerprint(current) === B.fingerprint(p.detailIssue));
      p.sync.textContent = PR.t("同步于 {time}", { time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) });
    } catch (error) {
      B.error(p, error);
      p.sync.textContent = PR.t("同步失败，请重试");
      if (!p.loaded) p.board.replaceChildren(W.empty(PR.t("无法读取当前项目的 Beads。请检查服务端 bd 安装和项目初始化状态。")));
    } finally { p.loading = false; p.refresh.disabled = false; }
  };
  B.render = p => {
    if (!p.loaded) return;
    const focused = document.activeElement?.dataset?.beadId;
    const { scrollLeft, scrollTop } = p.board;
    const issues = p.issues.filter(issue => B.matches(issue, p.filters, p.ready, p.blocked))
      .sort((a, b) => (a.priority ?? 2) - (b.priority ?? 2) || String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    const done = p.issues.filter(issue => issue.status === "closed").length;
    p.summary.textContent = PR.t("{total} 项任务 · {ready} 项就绪 · {done} 项完成", { total: p.issues.length, ready: p.ready.size, done });
    p.count.textContent = PR.t("显示 {n} / {total} 项", { n: issues.length, total: p.issues.length });
    p.board.replaceChildren(); p.board.classList.toggle("is-list", p.mode === "list");
    if (!issues.length) {
      p.board.append(W.empty(p.issues.length ? PR.t("没有符合筛选条件的任务") : PR.t("这个项目还没有任务。新建第一项任务，与 Agent 一起推进研究。"))); return;
    }
    const labels = { ...B.statuses(), other: PR.t("其他状态") };
    const lanes = Object.keys(labels).filter(key => key !== "other" || issues.some(issue => B.lane(issue, p.blocked) === key));
    if (p.mode === "list") issues.forEach(issue => p.board.append(card(issue)));
    else lanes.forEach(key => {
      const items = issues.filter(issue => B.lane(issue, p.blocked) === key);
      const lane = PR.el("section", { class: "ws-beads-lane", "data-status": key, "aria-label": labels[key] });
      lane.append(PR.el("h3", { text: labels[key] + " · " + items.length }));
      const cards = PR.el("div", { class: "ws-beads-cards" });
      items.forEach(issue => cards.append(card(issue)));
      if (!items.length) cards.append(PR.el("p", { class: "ws-beads-lane-empty", text: PR.t("暂无任务") }));
      lane.append(cards); p.board.append(lane);
    });
    p.board.scrollLeft = scrollLeft; p.board.scrollTop = scrollTop;
    if (focused) [...p.board.querySelectorAll("[data-bead-id]")].find(el => el.dataset.beadId === focused)?.focus({ preventScroll: true });
    function card(issue) {
      const button = PR.el("button", { type: "button", class: "ws-beads-card", "data-bead-id": issue.id, "aria-pressed": String(p.selected === issue.id) });
      button.onclick = () => B.show(p, issue.id);
      const meta = PR.el("span", { class: "ws-beads-card-meta" });
      meta.append(PR.el("code", { text: issue.id }), PR.el("span", { class: "ws-beads-priority", "data-priority": issue.priority, text: "P" + issue.priority }));
      const tags = PR.el("span", { class: "ws-beads-tags" });
      tags.append(PR.el("span", { text: B.types()[issue.issue_type] || issue.issue_type }));
      if (p.mode === "list") tags.append(PR.el("span", { text: labels[B.lane(issue, p.blocked)] }));
      if (p.ready.has(issue.id)) tags.append(PR.el("span", { class: "is-ready", text: PR.t("就绪") }));
      if (issue.assignee) tags.append(PR.el("span", { text: issue.assignee }));
      (issue.labels || []).slice(0, 3).forEach(label => tags.append(PR.el("span", { text: label })));
      button.append(meta, PR.el("strong", { text: issue.title }), tags); return button;
    }
  };
  B.open = () => {
    if (!W.project) return;
    if (B.panel?.dialog.open) return;
    const dialog = W.modal(PR.t("Beads 项目管理")); dialog.classList.add("ws-beads-dialog"); dialog.id = "ws-beads-panel";
    const p = B.panel = { dialog, pid: W.project.id, issues: [], ready: new Set(), blocked: new Set(), filters: { q: "", status: "", type: "", priority: "" }, mode: "board", detailSeq: 0, listSeq: 0, dirty: false };
    const base = W.base + "/" + encodeURIComponent(p.pid) + "/beads";
    p.request = (suffix, body) => W.request(base + suffix, body);
    const head = dialog.querySelector("header"), heading = PR.el("div");
    heading.append(head.querySelector("h2"), PR.el("p", { class: "ws-beads-project", text: W.project.name }), PR.el("p", { class: "hint", text: PR.t("仅当前项目 · 与原生 Agent 共用 Beads 数据") }));
    head.prepend(heading); head.querySelector("button").onclick = () => B.close(p);
    p.summary = PR.el("span", { class: "ws-beads-summary" }); p.sync = PR.el("span", { class: "hint" });
    p.refresh = W.iconButton(PR.t("刷新任务"), () => B.refresh(p), "sync");
    p.create = W.button(PR.t("新建任务"), () => B.show(p, null), "plus", "accent"); p.create.disabled = true;
    const toolbar = PR.el("div", { class: "ws-beads-toolbar" });
    const modes = PR.el("div", { class: "ws-beads-modes", role: "group", "aria-label": PR.t("任务视图") });
    [["board", PR.t("看板")], ["list", PR.t("列表")]].forEach(([mode, text]) => {
      const button = W.button(text, () => { p.mode = mode; [...modes.children].forEach(el => el.setAttribute("aria-pressed", String(el === button))); B.render(p); });
      button.setAttribute("aria-pressed", String(mode === p.mode)); modes.append(button);
    });
    toolbar.append(p.summary, modes, p.refresh, p.create);
    const filters = PR.el("div", { class: "ws-beads-filters" });
    const search = W.input("", { type: "search", placeholder: PR.t("搜索任务、负责人或标签"), "aria-label": PR.t("搜索任务、负责人或标签") });
    search.oninput = () => { p.filters.q = search.value.trim(); B.render(p); }; filters.append(search);
    [["status", PR.t("任务状态"), { "": PR.t("全部状态"), ready: PR.t("就绪"), ...B.statuses() }],
      ["type", PR.t("任务类型"), { "": PR.t("全部类型"), ...B.types() }],
      ["priority", PR.t("优先级"), { "": PR.t("全部优先级"), "0": "P0", "1": "P1", "2": "P2", "3": "P3", "4": "P4" }]].forEach(([key, label, choices]) => {
      const input = B.select(label, choices); input.onchange = () => { p.filters[key] = input.value; B.render(p); }; filters.append(input);
    });
    p.error = PR.el("p", { class: "ws-beads-error", role: "alert", hidden: true });
    p.truncated = PR.el("p", { class: "ws-beads-warning", text: PR.t("任务过多，当前仅显示部分结果。请使用原生 bd 查看完整项目。"), hidden: true });
    p.body = PR.el("div", { class: "ws-beads-body" });
    p.board = PR.el("div", { class: "ws-beads-board", "aria-label": PR.t("项目任务"), tabindex: 0 }); p.board.append(W.empty(PR.t("正在载入…")));
    p.detail = PR.el("aside", { class: "ws-beads-detail", "aria-label": PR.t("任务详情"), hidden: true }); p.body.append(p.board, p.detail);
    const foot = PR.el("footer", { class: "ws-beads-footer" });
    p.count = PR.el("span"); p.notice = PR.el("span", { role: "status" }); foot.append(p.count, p.notice, p.sync);
    dialog.append(toolbar, filters, p.error, p.truncated, p.body, foot);
    dialog.addEventListener("cancel", event => { event.preventDefault(); B.close(p); });
    dialog.addEventListener("keydown", event => { if (!event.target.closest(".pr-dropdown, .pr-dropdown-menu")) event.stopPropagation(); });
    dialog.addEventListener("close", () => { clearTimeout(p.timer); p.detailSeq++; if (B.panel === p) B.panel = null; W.$("ws-beads-toggle").setAttribute("aria-expanded", "false"); W.$("ws-beads-toggle").focus(); });
    W.$("ws-beads-toggle").setAttribute("aria-expanded", "true"); dialog.showModal(); search.focus();
    const poll = async () => {
      if (!B.live(p)) return;
      if (W.isVisible()) await B.refresh(p);
      if (B.live(p)) p.timer = setTimeout(poll, 10000);
    };
    poll();
  };
  B.init = () => {
    W.$("ws-beads-toggle").onclick = B.open;
    ["visibilitychange", "easyread:visibility"].forEach(event => document.addEventListener(event, () => {
      if (B.panel && B.live(B.panel) && W.isVisible()) B.refresh(B.panel);
    }));
    window.addEventListener("pagehide", () => { if (B.panel) clearTimeout(B.panel.timer); });
    window.addEventListener("beforeunload", event => { if (B.panel?.dirty || B.panel?.busy) { event.preventDefault(); event.returnValue = ""; } });
  };
})(window.PR, window.PR.workspaces);

(function (PR, W) {
  "use strict";
  const S = W.sessions = { current: null, history: null, engines: [], items: [], capabilities: {} };
  const busy = new Set();
  let listSignature = "", selectionSeq = 0, revision = 0, selecting = false, polling = false, loadingOlder = false;
  const route = id => "/sessions/" + encodeURIComponent(id);
  const draftKey = () => "message:" + (S.current?.id || "new");
  const mode = () => W.$("ws-session-mode").value;
  const capability = name => (S.current?.capabilities?.[name] ??
    S.capabilities.engines?.[S.current?.engine || W.project.engine || "codex"]?.[name]) === true;
  S.isPending = id => selecting || busy.has(id);
  const occupied = () => S.isPending(S.current?.id) || !!S.current?.terminal_open || W.sessionTerminal.owns(S.current?.id);
  const goalCommand = () => /^\/goal(?:\s+(.*))?$/s.exec(W.$("ws-message").value.trim());
  S.updateSend = () => {
    const command = goalCommand(), control = command && ["pause", "resume", "clear"].includes(command[1]);
    const supported = !(command || mode() === "goal") || capability("goal");
    W.$("ws-send").disabled = occupied() || W.sessionImages.pending || !supported || (!control && W.sessionBusy(S.current)) || (!W.$("ws-message").value.trim() && !W.mentions.files.length && !W.sessionImages.any);
    W.$("ws-steer").disabled = occupied() || W.sessionImages.pending || !capability("steer") || !S.current?.active_turn_id || (!W.$("ws-message").value.trim() && !W.mentions.files.length && !W.sessionImages.any);
  };
  S.persist = () => W.store(draftKey(), { message: W.$("ws-message").value, files: W.mentions.files, mode: mode() });
  S.hasDraft = () => !!W.$("ws-message").value || W.mentions.files.length > 0 || W.sessionImages.any || W.sessionImages.pending;
  function restoreDraft() {
    const draft = PR.ls.get(W.key(draftKey()), "");
    W.$("ws-message").value = typeof draft === "string" ? draft : draft?.message || "";
    W.$("ws-session-mode").value = draft?.mode === "goal" ? "goal" : "chat";
    W.mentions.restore(draft);
    W.sessionImages.restore(S.current?.id);
  }
  S.init = async () => {
    W.mentions.init(); restoreDraft();
    W.$("ws-session-select").onchange = W.run(event => S.select(event.target.value));
    W.$("ws-session-new").onclick = W.run(() => S.create());
    W.$("ws-session-mode").onchange = () => { S.persist(); S.render(); };
    W.$("ws-session-menu").onclick = event => PR.menu(event.currentTarget, [
      { label: PR.t("模型与思考强度"), icon: "gear", disabled: !W.sessionSettings.available(), fn: W.run(W.sessionSettings.open) },
      { label: PR.t("重命名会话"), icon: "edit", fn: W.run(async () => {
        if (!S.current) return;
        const { id, title: previous } = S.current;
        const title = await PR.promptText({ title: PR.t("会话名称"), value: previous, max: 200 });
        if (title) { await W.api(route(id) + "/rename", { title }); await S.refresh(); }
      }) },
      { label: PR.t("删除会话"), icon: "trash", disabled: !!S.current?.terminal_open || W.sessionTerminal.owns(S.current?.id), fn: W.run(async () => {
        const id = S.current?.id;
        if (!id || !await PR.confirm({ title: PR.t("删除会话？"), body: PR.t("将删除项目中的会话记录和此会话的输入草稿。正在运行的任务必须先停止。"), danger: true })) return;
        if (W.sessionTerminal.owns(id) || (S.current?.id === id && S.current.terminal_open)) return;
        await W.api(route(id) + "/delete", {}); W.sessionTerminal.dispose(id); PR.ls.del(W.key("message:" + id)); W.sessionImages.forget(id);
        if (S.current?.id === id) { W.$("ws-message").value = ""; W.mentions.restore(null); S.current = null; S.history = null; }
        await S.refresh(); if (!S.current) await S.select(S.items[0]?.id || "");
      }) }
    ]);
    W.$("ws-chat-form").onsubmit = W.run(async event => { event.preventDefault(); await S.send(); });
    W.$("ws-message").oninput = event => { S.persist(); S.updateSend(); if (!event.isComposing) W.run(W.mentions.update)(); };
    W.$("ws-message").oncompositionend = W.run(W.mentions.update);
    W.$("ws-message").onkeydown = event => {
      if (event.isComposing || event.keyCode === 229 || W.mentions.keydown(event)) return;
      if (event.key === "Enter" && (!event.shiftKey || event.ctrlKey || event.metaKey)) { event.preventDefault(); W.run(() => S.send())(); }
    };
    W.$("ws-cancel").onclick = W.run(() => S.current && (S.current.terminal_open || W.sessionTerminal.owns(S.current.id)
      ? W.sessionTerminal.stop() : S.mutate(S.current.id, "cancel", {})));
    W.$("ws-steer").onclick = W.run(() => S.send(true));
    ["pause", "resume", "clear"].forEach(action => { W.$("ws-goal-" + action).onclick = W.run(() => S.goal(action)); });
    W.$("ws-history-older").onclick = W.run(S.loadOlder);
    W.$("ws-history-latest").onclick = W.run(S.reload);
    W.sessionTerminal.init();
    await S.refresh();
    const remembered = PR.ls.get(W.key("session"), null);
    await S.select(S.items.some(item => item.id === remembered) ? remembered : S.items[0]?.id || "");
  };
  S.refresh = async () => {
    const before = revision, data = await W.api("/sessions");
    if (before === revision) W.sessionTerminal.reconcile(data.sessions || []);
    // List entries remain summaries even when reading legacy server records.
    S.items = (data.sessions || []).map(({ messages, events, pending_requests, ...summary }) => summary);
    S.engines = data.engines || []; S.capabilities = data.capabilities || {};
    const next = JSON.stringify(S.items.map(item => [item.id, item.title, W.sessionLabel(item)]));
    if (next === listSignature) return;
    listSignature = next;
    const select = W.$("ws-session-select"), current = selecting ? select.value : S.current?.id;
    select.replaceChildren();
    if (!S.items.length) select.append(PR.el("option", { value: "", text: PR.t("尚无会话") }));
    S.items.forEach(session => select.append(PR.el("option", { value: session.id, text: (session.title || session.id) + " · " + W.sessionLabel(session) })));
    if (current && S.items.some(item => item.id === current)) select.value = current;
  };
  S.select = async id => {
    if (!S.persist()) { W.$("ws-session-select").value = S.current?.id || ""; return; }
    const seq = ++selectionSeq, before = revision; selecting = true; loadingOlder = false; S.render();
    try {
      const data = id ? await W.api(route(id)) : null;
      if (seq !== selectionSeq || !S.persist()) return;
      S.history = data ? new W.SessionHistory(data) : null; S.current = S.history?.session || null;
      if (before === revision) W.sessionTerminal.observe(S.current);
      loadingOlder = false; W.store("session", id); W.$("ws-session-select").value = id;
      restoreDraft(); W.$("ws-messages").scrollTop = W.$("ws-messages").scrollHeight;
    } finally {
      if (seq === selectionSeq) { selecting = false; W.$("ws-session-select").value = S.current?.id || ""; S.render(); }
    }
  };
  S.reload = async (reset = false) => {
    const history = S.history, seq = selectionSeq, before = revision;
    if (!history) return;
    const data = await W.api(route(history.session.id));
    if (S.history !== history || seq !== selectionSeq || before !== revision || (reset !== true && data.cursor < history.cursor)) return;
    S.history = new W.SessionHistory(data); S.current = S.history.session; W.sessionTerminal.observe(S.current); S.render();
  };
  S.loadOlder = async () => {
    if (!S.history || loadingOlder || S.isPending(S.current?.id)) return;
    loadingOlder = true; S.render();
    const seq = selectionSeq;
    try {
      // Refresh the server boundary before paging: live caches may have evicted older rows.
      if (!S.history.older) await S.reload();
      if (seq !== selectionSeq) return;
      const history = S.history;
      if (!history.hasOlder || history.before == null) return;
      const data = await W.api(route(S.current.id) + "/history?" + new URLSearchParams({ before: history.before, limit: 40 }));
      if (S.history !== history || seq !== selectionSeq) return;
      const box = W.$("ws-messages"), height = box.scrollHeight, scroll = box.scrollTop;
      history.prepend(data); S.render(); box.scrollTop = Math.max(0, scroll + box.scrollHeight - height);
    } finally { if (seq === selectionSeq) { loadingOlder = false; S.render(); } }
  };
  S.mutate = async (id, operation, body) => {
    if (busy.has(id)) throw new Error(PR.t("会话正在处理请求，请稍后重试。"));
    const history = S.history, seq = selectionSeq; busy.add(id); ++revision; S.render();
    try {
      const data = await W.api(route(id) + "/" + operation, body);
      if (data.session) W.sessionTerminal.observe(data.session);
      if (data.session && S.current?.id === id && S.history === history && seq === selectionSeq) {
        history.snapshot(data); S.current = history.session;
      }
      return data;
    } finally { ++revision; busy.delete(id); S.render(); }
  };
  S.goal = async (action, objective) => {
    if (!S.current || occupied()) return;
    if (!capability("goal")) throw new Error(PR.t("当前引擎不支持原生目标。"));
    return S.mutate(S.current.id, "goal", { action, ...(objective ? { objective } : {}) });
  };
  S.respond = async (id, request, payload) => {
    if (occupied()) throw new Error(PR.t("会话正在处理请求，请稍后重试。"));
    if (S.current?.id !== id || !W.pendingRequest(S.current, request)) throw new Error(PR.t("请求已过期或已处理，请查看最新状态。"));
    try {
      return await S.mutate(id, "respond", { ...payload, request_id: request.id, generation: request.generation });
    } catch (error) {
      if ([409, 410].includes(error.status)) {
        W.error(new Error(PR.t("请求已过期或已处理，请查看最新状态。")));
        if (S.current?.id === id) await S.reload();
      }
      throw error;
    }
  };
  S.create = async () => {
    const dialog = W.modal(PR.t("新建 / 恢复原生会话")), form = PR.el("form");
    const engine = PR.el("select", { class: "input", required: true });
    ["codex", "claude", "pi"].forEach(id => {
      const found = S.engines.find(item => item.id === id);
      engine.append(PR.el("option", { value: id, disabled: found?.installed === false, text: W.engineName(id) + (found?.installed === false ? " · " + PR.t("未安装") : found?.version ? " · " + found.version : "") }));
    });
    engine.value = W.project.engine || "codex";
    if (engine.selectedOptions[0]?.disabled) engine.value = S.engines.find(item => item.installed)?.id || "";
    const title = W.field(PR.t("会话名称"), W.input("", { maxlength: 120 }), form);
    W.field(PR.t("Agent 引擎"), engine, form);
    const model = W.modelPicker(form, engine, dialog);
    W.sessionCapabilityHint(form, engine);
    const native = W.field(PR.t("原生会话 ID（可选，用于 resume）"), W.input("", { autocomplete: "off" }), form);
    const scope = PR.el("p", { class: "hint" }); form.append(scope);
    const scopeText = () => { scope.textContent = engine.value === "pi" ? PR.t("Pi 会话在项目目录中使用完整原生配置。恢复 ID 必须属于当前项目的可信会话。") : PR.t("会话在项目目录中运行，继承原生 CLI 的模型、MCP 和 Skills 配置。恢复 ID 必须属于当前项目的可信会话。"); };
    engine.addEventListener("change", scopeText); scopeText();
    const error = PR.el("p", { class: "err", role: "alert" }); form.append(error);
    const submit = PR.el("button", { class: "btn accent", type: "submit", text: PR.t("创建会话") }); form.append(submit);
    form.onsubmit = async event => {
      event.preventDefault(); submit.disabled = true;
      try {
        const data = await W.api("/sessions/create", { engine: engine.value, ...(title.value.trim() ? { title: title.value.trim() } : {}), ...(model.value.trim() ? { model: model.value.trim() } : {}), reasoning_effort: model.reasoningEffort, ...(native.value.trim() ? { native_id: native.value.trim() } : {}) });
        const initial = !S.current;
        if (initial) { W.store("message:" + data.session.id, { message: W.$("ws-message").value, files: W.mentions.files, mode: mode() }); W.sessionImages.copyTo(data.session.id); }
        await S.refresh(); await S.select(data.session.id);
        if (initial && S.current?.id === data.session.id) W.sessionImages.forget(null);
        dialog.close();
      } catch (e) { error.textContent = e.message; } finally { submit.disabled = false; }
    };
    dialog.append(form); dialog.showModal(); title.focus();
  };
  S.send = async (steer = false) => {
    S.updateSend();
    if (W.$(steer ? "ws-steer" : "ws-send").disabled || (steer && !capability("steer"))) return;
    const typed = W.$("ws-message").value.trim(), submittedMode = mode();
    const message = typed || (W.mentions.files.length ? PR.t("请查看引用的文件。") : W.sessionImages.any ? PR.t("请解释这张图片。") : "");
    if (!message) return;
    if (!S.current) { await S.create(); return; }
    const id = S.current.id, files = [...W.mentions.files], command = !steer && goalCommand();
    const control = command && ["pause", "resume", "clear"].includes(command[1]);
    const images = W.sessionImages.payload(), attachments = images.length ? { images } : {};
    if (images.length && !steer && (command || submittedMode === "goal")) throw new Error(PR.t("图片请使用普通执行模式发送，不支持原生目标。"));
    if (command && !command[1]) throw new Error(PR.t("请在 /goal 后输入目标，或使用 pause、resume、clear。"));
    W.mentions.close();
    if (steer) {
      try { await S.mutate(id, "steer", { message, files, ...attachments, expected_turn_id: S.current.active_turn_id }); }
      catch (error) {
        if ([409, 410].includes(error.status) && S.current?.id === id) await S.reload();
        throw error;
      }
    } else if (control) await S.goal(command[1]);
    else await S.mutate(id, "send", { message: command ? command[1] : message, files, ...attachments, mode: command ? "goal" : submittedMode });
    if (!control) W.sessionImages.sent(id, images);
    // A failed or stale request leaves the draft intact; never retry a write automatically.
    const unchanged = draft => draft?.message?.trim() === typed && JSON.stringify(draft.files || []) === JSON.stringify(files) && (draft.mode || "chat") === submittedMode;
    if (S.current?.id === id) {
      if (unchanged({ message: W.$("ws-message").value, files: W.mentions.files, mode: mode() })) {
        W.$("ws-message").value = ""; if (!control) W.mentions.restore(null); S.persist();
      }
    } else {
      const key = "message:" + id, draft = PR.ls.get(W.key(key), null);
      if (unchanged(draft)) W.store(key, { message: "", files: control ? files : [], mode: submittedMode });
    }
    S.render();
  };
  S.poll = async () => {
    if (polling) return;
    polling = true;
    try {
      await S.refresh();
      const history = S.history, seq = selectionSeq, before = revision;
      if (!history || selecting || loadingOlder || busy.has(S.current.id)) { S.render(); return; }
      const data = await W.api(route(S.current.id) + "/updates?" + new URLSearchParams({ after: history.cursor, limit: 100 }));
      if (S.history !== history || seq !== selectionSeq || before !== revision || busy.has(S.current.id)) return;
      if (!history.update(data)) { await S.reload(true); return; }
      S.current = history.session; W.sessionTerminal.observe(S.current); S.render();
    } finally { polling = false; }
  };
  S.render = () => {
    const session = S.current, goal = session?.goal, pending = occupied();
    S.updateSend();
    W.$("ws-send").hidden = false;
    W.$("ws-cancel").hidden = !W.sessionCanStop(session) || !!session?.terminal_open || W.sessionTerminal.owns(session?.id);
    W.$("ws-cancel").disabled = selecting || busy.has(session?.id);
    W.$("ws-steer").hidden = !capability("steer") || !session?.active_turn_id;
    W.$("ws-session-mode").querySelector('[value="goal"]').disabled = !capability("goal");
    W.$("ws-session-mode").disabled = pending;
    W.sessionTerminal.render();
    W.$("ws-session-menu").disabled = !session || selecting;
    W.$("ws-session-status").textContent = selecting || busy.has(session?.id) ? PR.t("正在连接…") : session ? W.sessionLabel(session) : "";
    W.$("ws-session-status").title = session?.native_id || "";
    W.$("ws-goal").hidden = !goal;
    W.$("ws-goal-objective").textContent = goal?.objective || "";
    W.$("ws-goal-objective").title = goal?.objective || "";
    W.$("ws-goal-state").textContent = W.goalStateLabel(goal?.status);
    W.$("ws-goal-pause").hidden = !["active", "blocked"].includes(goal?.status);
    W.$("ws-goal-resume").hidden = !["paused", "usageLimited", "budgetLimited"].includes(goal?.status);
    ["pause", "resume", "clear"].forEach(action => { W.$("ws-goal-" + action).disabled = pending || !capability("goal"); });
    const note = W.$("ws-session-notice");
    note.textContent = [
      session?.reconciliation_required ? PR.t("运行状态待核对。检查已有结果和外部任务后，可点击“停止”释放运行时；不会自动重提任务。") :
        session?.state === "interrupted" ? PR.t("运行已中断；请核对已有结果后再继续，不会自动重提任务。") : "",
      session?.engine === "pi" ? W.piSafetyWarning() + " " + PR.t("Pi 支持普通执行和恢复会话，不支持原生目标、运行中引导或交互式答复；不会自动注入 EasyRead MCP。") :
      session?.engine === "claude" ? PR.t("Claude 支持普通执行；不提供原生目标或交互式答复。") :
        session && !capability("goal") ? PR.t("当前运行时未提供原生目标；仍可使用普通执行。") : ""
    ].filter(Boolean).join(" ");
    note.hidden = !note.textContent;
    W.$("ws-history-older").hidden = !S.history?.hasOlder;
    W.$("ws-history-older").disabled = loadingOlder || S.isPending(session?.id);
    W.$("ws-history-latest").hidden = !S.history?.older;
    W.$("ws-history-latest").disabled = loadingOlder || S.isPending(session?.id);
    W.$("ws-history-status").textContent = S.history?.older ? PR.t("正在查看较早记录；新活动仍在后台更新。") : S.history?.catchingUp ? PR.t("正在同步最新活动…") : "";
    W.$("ws-history-tools").hidden = !S.history?.hasOlder && !S.history?.older && !S.history?.catchingUp;
    W.renderSessionRequests(session);
    W.$("ws-requests").inert = !!session?.terminal_open || W.sessionTerminal.owns(session?.id);
    W.renderConversation(S.history?.view || null);
  };
})(window.PR, window.PR.workspaces);

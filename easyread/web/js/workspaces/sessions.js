(function (PR, W) {
  "use strict";
  const S = W.sessions = { current: null, history: null, engines: [], items: [], capabilities: {}, loadError: null };
  const operations = new W.SessionOperations(), preparing = new Set();
  let listSignature = "", selectionSeq = 0, revision = 0, selecting = false, polling = false, loadingOlder = false;
  let initializing = false, retrying = false, pollErrors = 0, nextPoll = 0;
  const route = id => "/sessions/" + encodeURIComponent(id);
  const draftKey = () => "message:" + (S.current?.id || "new");
  const mode = () => W.$("ws-session-mode").value;
  const capability = name => (S.current?.capabilities?.[name] ??
    S.capabilities.engines?.[S.current?.engine || W.project.engine || "codex"]?.[name]) === true;
  S.supports = capability;
  S.isPending = id => selecting || operations.pending(id) || preparing.has(id);
  S.canControl = (id, operation) => !selecting && !S.current?.terminal_open && !W.sessionTerminal.owns(id) && operations.can(id, operation);
  const occupied = () => S.isPending(S.current?.id) || !!S.current?.terminal_open || W.sessionTerminal.owns(S.current?.id);
  const goalCommand = () => (S.current?.engine || W.project.engine) === "codex" ? /^\/goal(?:\s+(.*))?$/s.exec(W.$("ws-message").value.trim()) : null;
  S.updateSend = () => {
    const command = goalCommand(), control = command && ["pause", "resume", "clear"].includes(command[1]);
    const supported = !(command || mode() === "goal") || capability("goal");
    const unreadable = !!S.loadError || !!S.current?.native_sync_warning || (S.current?.engine === "pi" && !!S.current?.generation && !!S.current?.reconciliation_required);
    const text = W.$("ws-message").value.trim(), running = W.sessionBusy(S.current);
    const local = W.sessionCommands.isLocal(text) && !selecting && !preparing.has(S.current?.id) && !S.current?.terminal_open && !W.sessionTerminal.owns(S.current?.id);
    const nativeSlash = S.current?.engine === "pi" && capability("commands") && /^\/[^\s/]+/.test(text);
    const awaiting = !!S.current?.submission_pending || S.current?.runtime_phase === "compacting";
    const liveInput = capability("follow_up") || (capability("steer") && !!S.current?.active_turn_id) || nativeSlash;
    W.$("ws-send").disabled = !local && (unreadable || initializing || occupied() || awaiting || W.sessionImages.pending || !supported || (!control && running && !liveInput) || (!text && !W.mentions.files.length && !W.sessionImages.any));
    W.$("ws-steer").disabled = unreadable || initializing || occupied() || awaiting || W.sessionImages.pending || !capability("steer") || !S.current?.active_turn_id || (!text && !W.mentions.files.length && !W.sessionImages.any);
    const delivery = running && !control && !nativeSlash && !local ? (capability("follow_up") ? "follow-up" : capability("steer") ? "steer" : "send") : "send";
    const label = delivery === "follow-up" ? PR.t("排队") : delivery === "steer" ? PR.t("引导") : PR.t("发送");
    W.$("ws-send-label").textContent = label; W.$("ws-send-label").className = delivery === "send" ? "sr-only" : "";
    W.$("ws-send").title = delivery === "follow-up" ? PR.t("完成后继续") : delivery === "steer" ? PR.t("引导当前执行") : PR.t("发送");
    W.$("ws-send").setAttribute("aria-label", W.$("ws-send").title);
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
    initializing = true;
    const initialSelection = selectionSeq;
    W.mentions.init(); W.sessionCommands.init(); restoreDraft();
    W.$("ws-session-select").onchange = W.run(event => S.select(event.target.value));
    W.$("ws-session-new").onclick = W.run(() => S.create());
    W.$("ws-session-mode").onchange = () => { S.persist(); S.render(); };
    W.$("ws-session-menu").onclick = event => PR.menu(event.currentTarget, [
      { label: PR.t("模型与思考强度"), icon: "gear", disabled: !W.sessionSettings.available(), fn: W.run(W.sessionSettings.open) },
      { label: PR.t("命令与 Skills"), icon: "log", fn: W.run(W.sessionCommands.open) },
      { label: PR.t("会话信息"), icon: "log", fn: () => {
        if (!S.current) return;
        const { engine, native_id, model, reported_model, reasoning_effort, state, runtime_state } = S.current;
        const dialog = W.modal(PR.t("会话信息"));
        dialog.append(PR.el("pre", { text: JSON.stringify({ engine, native_id, model, reported_model, reasoning_effort, state, runtime_state }, null, 2) })); dialog.showModal();
      } },
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
    const complete = async () => { if (!await W.sessionCommands.update()) await W.mentions.update(); };
    W.$("ws-message").oninput = event => { S.persist(); S.updateSend(); if (!event.isComposing) W.run(complete)(); };
    W.$("ws-message").oncompositionend = W.run(complete);
    W.$("ws-message").onkeydown = event => {
      if (event.isComposing || event.keyCode === 229 || W.sessionCommands.keydown(event) || W.mentions.keydown(event)) return;
      if (event.key === "Enter" && (!event.shiftKey || event.ctrlKey || event.metaKey)) { event.preventDefault(); W.run(() => S.send())(); }
    };
    W.$("ws-cancel").onclick = W.run(() => S.current && (S.current.terminal_open || W.sessionTerminal.owns(S.current.id)
      ? W.sessionTerminal.stop() : S.mutate(S.current.id, "cancel", {})));
    W.$("ws-steer").onclick = W.run(() => S.send(true));
    ["pause", "resume", "clear"].forEach(action => { W.$("ws-goal-" + action).onclick = W.run(() => S.goal(action)); });
    W.$("ws-history-older").onclick = W.run(S.loadOlder);
    W.$("ws-history-latest").onclick = W.run(S.reload);
    W.sessionTerminal.init(); S.render();
    try {
      await S.refresh();
      if (selectionSeq !== initialSelection || S.current) return;
      const remembered = PR.ls.get(W.key("session"), null);
      await S.select(S.items.some(item => item.id === remembered) ? remembered : S.items[0]?.id || "");
    } catch (error) {
      if (selectionSeq === initialSelection || !S.current) S.loadError = error;
    } finally { initializing = false; S.render(); }
  };
  S.retry = async () => {
    if (retrying || initializing) return;
    retrying = true; nextPoll = 0; S.render();
    const previous = S.history, before = selectionSeq;
    try {
      await S.refresh();
      if (before !== selectionSeq) return;
      if (S.current && S.history) await S.reload(true);
      else {
        const remembered = PR.ls.get(W.key("session"), null);
        await S.select(S.items.some(item => item.id === remembered) ? remembered : S.items[0]?.id || "");
      }
    } catch (error) { if (S.history === previous) S.loadError = error; }
    finally { retrying = false; S.render(); }
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
    const seq = ++selectionSeq, before = revision; selecting = true; loadingOlder = false; W.sessionCommands.close(); S.render();
    try {
      const data = id ? await W.api(route(id)) : null;
      if (seq !== selectionSeq || !S.persist()) return;
      S.history = data ? new W.SessionHistory(data) : null; S.current = S.history?.session || null;
      S.loadError = null; pollErrors = 0; nextPoll = 0;
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
    S.history = new W.SessionHistory(data); S.current = S.history.session;
    S.loadError = null; pollErrors = 0; nextPoll = 0;
    W.sessionTerminal.observe(S.current); S.render();
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
    const release = operations.begin(id, operation);
    if (!release) throw new Error(PR.t("会话正在处理请求，请稍后重试。"));
    const history = S.history, seq = selectionSeq; ++revision; S.render();
    try {
      const data = await W.api(route(id) + "/" + operation, body);
      if (data.session) W.sessionTerminal.observe(data.session);
      if (data.session && S.current?.id === id && S.history === history && seq === selectionSeq) {
        history.snapshot(data); S.current = history.session;
      }
      return data;
    } finally { ++revision; release(); S.render(); }
  };
  S.goal = async (action, objective) => {
    if (!S.current || occupied()) return;
    if (!capability("goal")) throw new Error(PR.t("当前引擎不支持原生目标。"));
    return S.mutate(S.current.id, "goal", { action, ...(objective ? { objective } : {}) });
  };
  S.respond = async (id, request, payload) => {
    if (!S.canControl(id, "respond")) throw new Error(PR.t("会话正在处理请求，请稍后重试。"));
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
    const native = W.field(PR.t("原生会话 ID（可选，用于 resume）"), W.input("", { autocomplete: "off" }), form);
    const scope = PR.el("p", { class: "hint" }); form.append(scope);
    scope.textContent = PR.t("恢复 ID 必须属于当前项目。");
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
    const typed = W.$("ws-message").value.trim();
    if (W.sessionCommands.isLocal(typed)) {
      if (await W.sessionCommands.executeLocal(typed)) { W.sessionCommands.close(); S.render(); return; }
    }
    S.updateSend();
    if (W.$(steer ? "ws-steer" : "ws-send").disabled || (steer && !capability("steer"))) return;
    const submittedMode = mode();
    const message = typed || (W.mentions.files.length ? PR.t("请查看引用的文件。") : W.sessionImages.any ? PR.t("请解释这张图片。") : "");
    if (!message) return;
    W.focusWorkspaceCompanion?.();
    if (!S.current) { await S.create(); return; }
    const id = S.current.id, files = [...W.mentions.files], turn = S.current.active_turn_id, generation = S.current.generation;
    const command = !steer && goalCommand(), control = command && ["pause", "resume", "clear"].includes(command[1]);
    const images = W.sessionImages.payload(), attachments = images.length ? { images } : {};
    if (images.length && !steer && (command || submittedMode === "goal")) throw new Error(PR.t("图片请使用普通执行模式发送，不支持原生目标。"));
    if (command && !command[1]) throw new Error(PR.t("请在 /goal 后输入目标，或使用 pause、resume、clear。"));
    if (preparing.has(id)) return;
    preparing.add(id); S.render();
    try {
      const resolved = !command && /^\s*[/$]/.test(message) ? await W.sessionCommands.resolve(message) : {message,nativeCommand:false};
      if (S.current?.id !== id || W.$("ws-message").value.trim() !== typed || mode() !== submittedMode) throw new Error(PR.t("会话或输入已变化，请重新确认后发送。"));
      W.mentions.close(); W.sessionCommands.close();
      let operation = steer ? "steer" : "send";
      if (!steer && !control && !command && submittedMode !== "goal" && W.sessionBusy(S.current) && !resolved.nativeCommand) {
        operation = capability("follow_up") ? "follow-up" : capability("steer") ? "steer" : "send";
      }
      let result;
      if (control) result = await S.mutate(id, "goal", { action: command[1] });
      else if (operation === "steer" || operation === "follow-up") {
        try { result = await S.mutate(id, operation, { message: resolved.message, files, ...attachments, expected_turn_id: turn, ...(generation ? {generation} : {}) }); }
        catch (error) {
          if ([409, 410].includes(error.status) && S.current?.id === id) await S.reload();
          throw error;
        }
      } else result = await S.mutate(id, "send", { message: command ? command[1] : resolved.message, files, ...attachments, mode: command ? "goal" : submittedMode });
      // Failed or ambiguous receipts retain the editor. A locally journaled
      // asynchronous submission remains visible until the native CLI confirms it.
      if (["failed", "unknown", "cleared"].includes(result?.submission?.status)) return;
      if (!control) W.sessionImages.sent(id, images);
      const unchanged = draft => draft?.message?.trim() === typed && JSON.stringify(draft.files || []) === JSON.stringify(files) && (draft.mode || "chat") === submittedMode;
      if (S.current?.id === id) {
        if (unchanged({ message: W.$("ws-message").value, files: W.mentions.files, mode: mode() })) {
          W.$("ws-message").value = ""; if (!control) W.mentions.restore(null); S.persist();
        }
      } else {
        const key = "message:" + id, draft = PR.ls.get(W.key(key), null);
        if (unchanged(draft)) W.store(key, { message: "", files: control ? files : [], mode: submittedMode });
      }
    } finally { preparing.delete(id); S.render(); }
  };
  S.poll = async () => {
    if (polling || initializing || retrying || Date.now() < nextPoll) return;
    polling = true;
    try {
      await S.refresh();
      const history = S.history, seq = selectionSeq, before = revision;
      if (!history || selecting || loadingOlder) { S.render(); return; }
      const data = await W.api(route(S.current.id) + "/updates?" + new URLSearchParams({ after: history.cursor, limit: 100 }));
      if (S.history !== history || seq !== selectionSeq || before !== revision) return;
      if (!history.update(data)) { await S.reload(true); return; }
      S.current = history.session; S.loadError = null; pollErrors = 0; nextPoll = 0;
      W.sessionTerminal.observe(S.current); S.render();
    } catch (error) {
      // Session history errors belong to this pane. Files/LaTeX keep refreshing,
      // and a bad record is not evidence that the SSH connection is offline.
      S.loadError = error; pollErrors = Math.min(pollErrors + 1, 5);
      nextPoll = Date.now() + Math.min(30000, 1800 * (2 ** pollErrors));
      S.render();
    } finally { polling = false; }
  };
  S.render = () => {
    const session = S.current, goal = session?.goal, pending = occupied();
    S.updateSend();
    W.$("ws-send").hidden = false;
    W.$("ws-cancel").hidden = !W.sessionCanStop(session) || !!session?.terminal_open || W.sessionTerminal.owns(session?.id);
    W.$("ws-cancel").disabled = !S.canControl(session?.id, "cancel");
    W.$("ws-steer").hidden = !capability("steer") || !capability("follow_up") || !session?.active_turn_id;
    W.$("ws-session-mode").querySelector('[value="goal"]').disabled = !capability("goal");
    W.$("ws-session-mode").disabled = pending;
    // A one-option selector is not a setting. Keep unsupported saved Goal mode
    // visible so the user can explicitly return to ordinary execution.
    W.$("ws-session-mode").hidden = !capability("goal") && mode() !== "goal";
    W.sessionTerminal.render();
    W.$("ws-session-menu").disabled = !session || selecting;
    W.$("ws-session-status").textContent = selecting || operations.pending(session?.id) ? PR.t("正在连接…") : session ? W.sessionLabel(session) : "";
    W.$("ws-session-status").title = session?.native_id || "";
    W.$("ws-goal").hidden = !goal;
    W.$("ws-goal-objective").textContent = goal?.objective || "";
    W.$("ws-goal-objective").title = goal?.objective || "";
    W.$("ws-goal-state").textContent = W.goalStateLabel(goal?.status);
    W.$("ws-goal-pause").hidden = !["active", "blocked"].includes(goal?.status);
    W.$("ws-goal-resume").hidden = !["paused", "usageLimited", "budgetLimited"].includes(goal?.status);
    ["pause", "resume", "clear"].forEach(action => { W.$("ws-goal-" + action).disabled = pending || !capability("goal"); });
    const note = W.$("ws-session-notice");
    const details = [S.loadError?.message, session?.native_sync_warning?.message, session?.error,
      session?.reconciliation_required ? PR.t("运行状态待核对。检查已有结果和外部任务后，可点击“停止”释放运行时；不会自动重提任务。") :
        session?.state === "interrupted" ? PR.t("运行已中断；请核对已有结果后再继续，不会自动重提任务。") : ""].filter(Boolean).join("\n\n");
    note.textContent = S.loadError ? PR.t("会话读取失败，项目文件仍可使用。") : session?.native_sync_warning ? PR.t("记录待核对") :
      session?.reconciliation_required ? PR.t("运行状态待核对") : session?.state === "interrupted" ? PR.t("运行已中断") : "";
    note.hidden = !note.textContent;
    if (details) note.append(document.createTextNode(" "), W.button(PR.t("详情"), () => {
      const dialog = W.modal(PR.t("会话状态")); dialog.append(PR.el("pre", { text: details })); dialog.showModal();
    }, null, "line"));
    if (S.loadError || session?.native_sync_warning) {
      const retry = W.button(PR.t("重新读取会话"), S.retry, "sync", "line");
      retry.disabled = retrying || initializing;
      note.append(document.createTextNode(" "), retry);
    }
    W.$("ws-history-older").hidden = !S.history?.hasOlder;
    W.$("ws-history-older").disabled = loadingOlder || S.isPending(session?.id);
    W.$("ws-history-latest").hidden = !S.history?.older;
    W.$("ws-history-latest").disabled = loadingOlder || S.isPending(session?.id);
    W.$("ws-history-status").textContent = S.history?.older ? PR.t("正在查看较早记录；新活动仍在后台更新。") : S.history?.catchingUp ? PR.t("正在同步最新活动…") : "";
    W.$("ws-history-tools").hidden = !S.history?.hasOlder && !S.history?.older && !S.history?.catchingUp;
    W.renderSessionRequests(session);
    W.$("ws-requests").inert = !!session?.terminal_open || W.sessionTerminal.owns(session?.id);
    W.renderConversation(S.history?.view || null);
    W.sessionQueue.render(session);
  };
})(window.PR, window.PR.workspaces);

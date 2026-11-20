(function (PR, W) {
  "use strict";
  const S = W.sessions = { current: null, engines: [], items: [] };
  let signature = "", listSignature = "", selectionSeq = 0, busy = false, terminalWindow;
  const draftKey = () => "message:" + (S.current?.id || "new");
  S.updateSend = () => { W.$("ws-send").disabled = busy || S.current?.state === "running" || S.current?.terminal_open || (!W.$("ws-message").value.trim() && !W.mentions.files.length); };
  const stateLabel = state => ({ idle: PR.t("就绪"), running: PR.t("运行中"), error: PR.t("出错"), interrupted: PR.t("已中断") }[state] || state || "");
  S.persist = () => W.store(draftKey(), { message: W.$("ws-message").value, files: W.mentions.files });
  S.hasDraft = () => !!W.$("ws-message").value || W.mentions.files.length > 0;
  S.init = async () => {
    W.mentions.init();
    const initial = PR.ls.get(W.key(draftKey()), "");
    W.$("ws-message").value = typeof initial === "string" ? initial : initial?.message || "";
    W.mentions.restore(initial);
    W.$("ws-session-select").onchange = W.run(event => S.select(event.target.value));
    W.$("ws-session-new").onclick = W.run(() => S.create());
    W.$("ws-session-menu").onclick = event => PR.menu(event.currentTarget, [
      { label: PR.t("重命名会话"), icon: "edit", fn: W.run(async () => {
        if (!S.current) return;
        const title = await PR.promptText({ title: PR.t("会话名称"), value: S.current.title, max: 200 });
        if (title) { await W.api("/sessions/" + encodeURIComponent(S.current.id) + "/rename", { title }); await S.refresh(); }
      }) },
      { label: PR.t("删除会话"), icon: "trash", fn: W.run(async () => {
        if (!S.current || !await PR.confirm({ title: PR.t("删除会话？"), body: PR.t("将删除项目中的会话记录和此会话的输入草稿。正在运行的任务必须先停止。"), danger: true })) return;
        const id = S.current.id; await W.api("/sessions/" + encodeURIComponent(id) + "/delete", {});
        PR.ls.del(W.key("message:" + id)); W.$("ws-message").value = ""; W.mentions.restore(null); S.current = null; await S.refresh();
        await S.select(S.items[0]?.id || "");
      }) }
    ]);
    W.$("ws-chat-form").onsubmit = W.run(async event => { event.preventDefault(); await S.send(); });
    W.$("ws-message").oninput = event => { S.persist(); S.updateSend(); if (!event.isComposing) W.run(W.mentions.update)(); };
    W.$("ws-message").oncompositionend = W.run(W.mentions.update);
    W.$("ws-message").onkeydown = event => {
      if (event.isComposing || event.keyCode === 229) return;
      if (W.mentions.keydown(event)) return;
      if (event.key === "Enter" && (!event.shiftKey || event.ctrlKey || event.metaKey)) { event.preventDefault(); W.run(() => S.send())(); }
    };
    W.$("ws-cancel").onclick = W.run(async () => {
      if (!S.current) return;
      const id = S.current.id, result = await W.api("/sessions/" + encodeURIComponent(id) + "/cancel", {});
      if (S.current?.id === id) { S.current = result.session; S.render(); }
    });
    const capability = W.capabilities.terminal || {};
    W.$("ws-terminal").disabled = !capability.available;
    W.$("ws-terminal").title = capability.available ? PR.t("打开原生 CLI 终端") : capability.reason || PR.t("原生终端需要服务器安装 ttyd；聊天仍可使用完整 Agent 工具。");
    W.$("ws-terminal-note").hidden = !!capability.available;
    W.$("ws-terminal-note").textContent = W.$("ws-terminal").title;
    W.$("ws-terminal").onclick = W.run(async () => {
      if (!S.current || S.current.state === "running" || busy) return;
      // Open on the trusted user gesture, not after a fetch (popup blockers).
      terminalWindow = window.open("about:blank", "_blank");
      if (terminalWindow) terminalWindow.opener = null;
      busy = true; S.render();
      try {
        const data = await W.api("/sessions/" + encodeURIComponent(S.current.id) + "/terminal", {});
        const url = new URL(data.url, location.href);
        if (!["http:", "https:"].includes(url.protocol)) throw new Error(PR.t("终端地址无效"));
        if (terminalWindow) terminalWindow.location = url.href;
        else {
          const dialog = W.modal(PR.t("打开原生 CLI 终端"));
          dialog.append(PR.el("a", { class: "btn accent", href: url.href, target: "_blank", rel: "noopener", text: PR.t("打开终端") })); dialog.showModal();
        }
        await S.poll();
      } catch (error) { terminalWindow?.close(); throw error; } finally { busy = false; S.render(); }
    });
    await S.refresh();
    const remembered = PR.ls.get(W.key("session"), null);
    await S.select(S.items.some(item => item.id === remembered) ? remembered : S.items[0]?.id || "");
  };
  S.refresh = async () => {
    const data = await W.api("/sessions"); S.items = data.sessions || []; S.engines = data.engines || [];
    const next = JSON.stringify(S.items.map(({ id, title, engine, state }) => ({ id, title, engine, state }))); if (next === listSignature) return; listSignature = next;
    const select = W.$("ws-session-select"), current = S.current?.id;
    select.replaceChildren();
    if (!S.items.length) select.append(PR.el("option", { value: "", text: PR.t("尚无会话") }));
    S.items.forEach(session => select.append(PR.el("option", { value: session.id, text: (session.title || session.id) + " · " + session.engine + " · " + stateLabel(session.state) })));
    if (current && S.items.some(item => item.id === current)) select.value = current;
  };
  S.select = async id => {
    if (!S.persist()) { W.$("ws-session-select").value = S.current?.id || ""; return; }
    const seq = ++selectionSeq;
    const session = id ? (await W.api("/sessions/" + encodeURIComponent(id))).session : null;
    if (seq !== selectionSeq) return;
    if (!S.persist()) return;
    S.current = session; signature = ""; W.store("session", id);
    W.$("ws-session-select").value = id;
    const draft = PR.ls.get(W.key(draftKey()), "");
    W.$("ws-message").value = typeof draft === "string" ? draft : draft?.message || "";
    W.mentions.restore(draft); S.render();
  };
  S.create = async () => {
    const dialog = W.modal(PR.t("新建 / 恢复原生会话")), form = PR.el("form");
    const engine = PR.el("select", { class: "input", required: true });
    ["codex", "claude"].forEach(id => {
      const found = S.engines.find(item => item.id === id);
      engine.append(PR.el("option", { value: id, disabled: found?.installed === false, text: (id === "codex" ? "Codex" : "Claude Code") + (found?.installed === false ? " · " + PR.t("未安装") : found?.version ? " · " + found.version : "") }));
    });
    engine.value = W.project.engine || "codex";
    if (engine.selectedOptions[0]?.disabled) engine.value = S.engines.find(item => item.installed)?.id || "";
    const title = W.field(PR.t("会话名称"), W.input("", { maxlength: 120 }), form);
    W.field(PR.t("Agent 引擎"), engine, form);
    const model = W.field(PR.t("模型（留空使用 CLI 默认配置）"), W.input(), form);
    const native = W.field(PR.t("原生会话 ID（可选，用于 resume）"), W.input("", { autocomplete: "off" }), form);
    form.append(PR.el("p", { class: "hint", text: PR.t("会话在项目目录中运行，继承原生 CLI 的模型、MCP 和 Skills 配置。恢复 ID 必须属于当前项目的可信会话。") }));
    const error = PR.el("p", { class: "err", role: "alert" }); form.append(error);
    const submit = PR.el("button", { class: "btn accent", type: "submit", text: PR.t("创建会话") }); form.append(submit);
    form.onsubmit = async event => {
      event.preventDefault(); submit.disabled = true;
      try {
        const data = await W.api("/sessions/create", { engine: engine.value, ...(title.value.trim() ? { title: title.value.trim() } : {}), ...(model.value.trim() ? { model: model.value.trim() } : {}), ...(native.value.trim() ? { native_id: native.value.trim() } : {}) });
        if (!S.current) W.store("message:" + data.session.id, { message: W.$("ws-message").value, files: W.mentions.files });
        await S.refresh(); await S.select(data.session.id); dialog.close();
      } catch (e) { error.textContent = e.message; } finally { submit.disabled = false; }
    };
    dialog.append(form); dialog.showModal(); title.focus();
  };
  S.send = async () => {
    const typed = W.$("ws-message").value.trim();
    const message = typed || (W.mentions.files.length ? PR.t("请查看引用的文件。") : "");
    if (!message || busy || S.current?.state === "running" || S.current?.terminal_open) return;
    if (!S.current) { await S.create(); return; }
    const id = S.current.id, files = [...W.mentions.files]; busy = true; W.mentions.close(); S.render();
    try {
      const data = await W.api("/sessions/" + encodeURIComponent(id) + "/send", { message, files });
      // A network error leaves the draft intact; never retry send automatically.
      if (S.current?.id === id) {
        S.current = data.session;
        if (W.$("ws-message").value.trim() === typed && JSON.stringify(W.mentions.files) === JSON.stringify(files)) { W.$("ws-message").value = ""; W.mentions.restore(null); S.persist(); }
      } else {
        const draft = PR.ls.get(W.key("message:" + id), null);
        if (draft?.message?.trim() === typed && JSON.stringify(draft.files || []) === JSON.stringify(files)) W.store("message:" + id, { message: "", files: [] });
      }
    } finally { busy = false; S.render(); }
  };
  S.poll = async () => {
    await S.refresh();
    const id = S.current?.id;
    if (!id || busy) return;
    const data = await W.api("/sessions/" + encodeURIComponent(id));
    if (id !== S.current?.id || busy) return;
    S.current = data.session; S.render();
  };
  S.render = () => {
    const session = S.current, running = session?.state === "running" || session?.terminal_open, box = W.$("ws-messages");
    S.updateSend(); W.$("ws-send").hidden = !!running;
    W.$("ws-cancel").hidden = !running;
    W.$("ws-terminal").disabled = !session || running || busy || !W.capabilities.terminal?.available;
    W.$("ws-session-menu").disabled = !session;
    W.$("ws-session-status").textContent = busy ? PR.t("正在连接…") : session ? session.engine + " · " + stateLabel(session.terminal_open ? "running" : session.state) : "";
    W.$("ws-session-status").title = session?.native_id || "";
    const next = JSON.stringify([session?.id, session?.state, session?.messages, session?.events, session?.error]);
    if (next === signature) return;
    signature = next;
    const scroll = box.scrollTop, atEnd = box.scrollHeight - scroll - box.clientHeight < 80;
    W.captureActivity(box); box.replaceChildren();
    if (!session?.messages?.length) {
      const empty = W.empty(PR.t("今天想研究什么？"));
      empty.append(PR.el("p", { class: "hint", text: PR.t("在项目目录中研究、编写文件，或通过 EasyRead MCP 检索论文。选择或新建原生会话开始。") })); box.append(empty);
    }
    const turns = W.conversationTurns(session);
    turns.forEach((turn, turnIndex) => {
      const round = PR.el("section", { class: "ws-conversation-turn", "data-turn-id": turn.key });
      let activityRendered = false;
      turn.messages.forEach(message => {
      if (message.role !== "user" && !activityRendered) { W.renderActivity(round, session, turn); activityRendered = true; }
      const assistant = message.role === "assistant";
      const article = PR.el("article", { class: "ws-message ws-message-" + (message.role === "user" ? "user" : "agent") + (assistant ? " cm ai" : "") });
      const who = PR.el(assistant ? "div" : "strong", { class: assistant ? "who" : "", text: message.role === "user" ? PR.t("你") : assistant ? (session.reported_model || session.model || session.engine) : PR.t("工具") });
      if (assistant) { const icon = PR.el("span", { class: "av" }); icon.innerHTML = PR.icon("sparkle", "sm"); who.prepend(icon); }
      article.append(who);
      const body = PR.el("div", { class: "ws-markdown" + (assistant ? " body" : "") });
      const content = typeof message.content === "string" ? message.content : JSON.stringify(message.content, null, 2);
      // Same parser and message typography as the reader; no parallel Markdown implementation.
      body.innerHTML = PR.mdBlocks(content || "", W.fileLinks.options()); article.append(body);
      if (Array.isArray(message.files) && message.files.length) {
        const refs = PR.el("div", { class: "ws-message-files" });
        message.files.forEach(path => refs.append(W.button(path, async () => {
          const valid = W.safePath(path);
          if (W.view === "latex" && !valid.startsWith("latex-workspace/") && !await W.switchView("project")) return;
          await W.files.open(valid);
        }, "page")));
        article.append(refs);
      }
      if (assistant && !message.partial) {
        const actions = PR.el("div", { class: "acts" });
        actions.append(W.button(PR.t("复制"), async () => { await PR.copyText(content); PR.toast(PR.t("已复制")); }, "copy"),
          W.button(PR.t("导出 Markdown"), () => PR.saveExport(new Blob([content + "\n"], { type: "text/markdown;charset=utf-8" }), session.engine + "-answer.md"), "download"));
        article.append(actions);
      }
      round.append(article);
      });
      if (!activityRendered) W.renderActivity(round, session, turn);
      if (turnIndex === turns.length - 1 && session?.state === "error" && session.error) round.append(PR.el("p", { class: "err", text: session.error }));
      box.append(round);
    });
    box.scrollTop = atEnd ? box.scrollHeight : scroll;
  };
})(window.PR, window.PR.workspaces);

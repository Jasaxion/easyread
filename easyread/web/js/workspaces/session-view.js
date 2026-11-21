/* Transcript rendering stays separate from transport and composer state. */
(function (PR, W) {
  "use strict";
  let signature = "";
  W.sessionStateLabel = state => ({ idle: PR.t("就绪"), running: PR.t("运行中"), error: PR.t("出错"), interrupted: PR.t("已中断"),
    queued: PR.t("排队中"), waiting_user: PR.t("等待回答"), waiting_external: PR.t("等待外部任务"), paused: PR.t("已暂停"), completed: PR.t("已完成") }[state] || state || "");
  W.goalStateLabel = state => ({ active: PR.t("目标进行中"), paused: PR.t("目标已暂停"), blocked: PR.t("目标等待处理"),
    usageLimited: PR.t("目标达到用量限制"), budgetLimited: PR.t("目标达到预算限制"), complete: PR.t("目标已完成") }[state] || state || "");
  W.sessionLabel = session => [session.engine, session.goal ? W.goalStateLabel(session.goal.status) : W.sessionStateLabel(session.terminal_open ? "running" : session.state),
    session.goal && session.state !== "idle" ? W.sessionStateLabel(session.state) : "",
    session.pending_count ? PR.t("{n} 项待处理", { n: session.pending_count }) : "",
    session.runtime_state === "sleeping" && !session.terminal_open ? PR.t("运行时休眠") : ""].filter(Boolean).join(" · ");
  W.renderConversation = session => {
    const box = W.$("ws-messages");
    const next = JSON.stringify([session?.id, session?.state, session?.goal, session?.active_turn_id, session?.viewing_history, session?.messages, session?.events, session?.error]);
    if (next === signature) return;
    signature = next;
    const scroll = box.scrollTop, atEnd = box.scrollHeight - scroll - box.clientHeight < 80;
    W.captureActivity(box); box.replaceChildren();
    const turns = W.conversationTurns(session);
    if (!turns.length) {
      const empty = W.empty(PR.t("今天想研究什么？"));
      empty.append(PR.el("p", { class: "hint", text: PR.t("在项目目录中研究、编写文件，或通过 EasyRead MCP 检索论文。选择或新建原生会话开始。") })); box.append(empty);
    }
    if (!turns.length && session?.error) box.append(PR.el("p", { class: "err", text: session.error }));
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

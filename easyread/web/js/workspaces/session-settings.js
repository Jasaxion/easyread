/* Model/effort edits preserve the native identity and only apply to an idle session. */
(function (PR, W) {
  "use strict";
  const settings = W.sessionSettings = {};
  settings.available = () => {
    const S = W.sessions, session = S.current;
    return !!session && !S.loadError && !session.native_sync_warning && !W.sessionOwnsWork(session) &&
      !S.isPending(session.id) && !W.sessionTerminal.owns(session.id);
  };
  settings.open = async () => {
    if (!settings.available()) return;
    const S = W.sessions, session = S.current, id = session.id;
    const dialog = W.modal(PR.t("模型与思考强度")), form = PR.el("form");
    const engine = PR.el("select", { class: "input", disabled: true });
    engine.append(PR.el("option", { value: session.engine, text: W.engineName(session.engine) })); engine.value = session.engine;
    W.field(PR.t("Agent 引擎"), engine, form);
    const model = W.modelPicker(form, engine, dialog, session);
    W.sessionCapabilityHint(form, engine);
    form.append(PR.el("p", { class: "hint", text: PR.t("仅修改无活动任务的会话，保留原生 ID 和历史。运行中或终端占用时，请先停止任务或关闭终端。") }));
    const error = PR.el("p", { class: "err", role: "alert" });
    const submit = PR.el("button", { class: "btn accent", type: "submit", text: PR.t("保存") });
    form.append(error, submit);
    form.onsubmit = async event => {
      event.preventDefault(); submit.disabled = true; error.textContent = "";
      try {
        if (S.current?.id !== id || !settings.available()) throw new Error(PR.t("会话状态已变化，请回到空闲会话后重试。"));
        await S.mutate(id, "settings", { model: model.value, reasoning_effort: model.reasoningEffort });
        dialog.close(); await S.refresh();
      } catch (e) { error.textContent = e.message; } finally { submit.disabled = false; }
    };
    dialog.append(form); dialog.showModal();
  };
})(window.PR, window.PR.workspaces);

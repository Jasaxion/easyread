(function (PR, W) {
  "use strict";
  W.openGit = async () => {
    const dialog = W.modal(PR.t("Git 同步"));
    const status = PR.el("pre", { class: "ws-git-output", role: "status" });
    const form = PR.el("div"), error = PR.el("p", { class: "err", role: "alert" });
    form.append(PR.el("p", { class: "hint", text: PR.t("仅在点击相应按钮时运行 Git。不自动推送，不执行强制推送。提交只包含磁盘上的文件；请先保存草稿。") }));
    const message = W.field(PR.t("提交说明"), W.input(), form);
    const remote = W.field(PR.t("远程仓库 URL"), W.input("", { placeholder: "https://…" }), form);
    const actions = PR.el("div", { class: "ws-row ws-wrap" });
    let busy = false;
    const show = data => { status.textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2); };
    const run = async (action, body = {}) => {
      if (busy) return;
      if (action === "commit" && !message.value.trim()) { error.textContent = PR.t("请填写提交说明。"); return; }
      if (action === "remote" && !remote.value.trim()) { error.textContent = PR.t("请填写远程仓库地址。"); return; }
      if ((action === "pull" || action === "commit") && W.files.hasDrafts()) {
        error.textContent = PR.t("请先保存或解决所有文件草稿，再进行 Git 操作。"); return;
      }
      if (action !== "status" && !await PR.confirm({ title: PR.t("执行 Git {action}？", { action }), body: action === "push" ? PR.t("将已提交的项目文件上传到配置的远程仓库。请确认没有凭据或私密文件。") : PR.t("此操作会更改项目的 Git 状态。") })) return;
      busy = true; [...actions.querySelectorAll("button")].forEach(button => button.disabled = true); error.textContent = "";
      try { show(await W.api("/git", { action, ...body })); await W.files.refresh(); }
      catch (e) { error.textContent = e.message; }
      finally { busy = false; [...actions.querySelectorAll("button")].forEach(button => button.disabled = false); }
    };
    actions.append(W.button(PR.t("刷新状态"), () => run("status"), "sync"), W.button(PR.t("提交"), () => run("commit", { message: message.value.trim() }), "check", "line"), W.button(PR.t("设置远程"), () => run("remote", { url: remote.value.trim() }), "link", "line"), W.button(PR.t("拉取"), () => run("pull"), "download", "line"), W.button(PR.t("推送"), () => run("push"), "upload", "line"));
    dialog.append(form, actions, error, status); dialog.showModal();
    try {
      const data = await W.api("/git"); show(data);
      const remotes = data.remotes;
      if (Array.isArray(remotes)) remote.value = remotes[0]?.url || "";
      else if (remotes && typeof remotes === "object") remote.value = typeof remotes.origin === "string" ? remotes.origin : remotes.origin?.url || "";
    } catch (e) { error.textContent = e.message; }
  };
})(window.PR, window.PR.workspaces);

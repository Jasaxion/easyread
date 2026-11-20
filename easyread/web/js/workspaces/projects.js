(function (PR, W) {
  "use strict";
  const P = W.projects = {};
  let listSeq = 0, olderOpen = false;
  P.refresh = async () => {
    const seq = ++listSeq;
    const data = await W.request(W.base + "?" + new URLSearchParams({ q: W.$("ws-project-search").value }));
    if (seq !== listSeq) return;
    PR.token = data.token; W.capabilities = data.capabilities || {}; W.libraryId = data.library_id;
    // Do not put private drafts in a fallback namespace shared by unrelated libraries.
    if (!W.libraryId) throw new Error(PR.t("服务器未提供文献库标识，请升级后端后重试。"));
    P.items = data.projects || [];
    const list = W.$("ws-project-list"); list.replaceChildren();
    W.$("ws-project-count").textContent = PR.t("研究项目 · {n}", { n: P.items.length });
    const older = PR.el("details", { class: "ws-older-projects" });
    const cutoff = Date.now() - 7 * 86400000;
    const oldCount = P.items.filter(project => Date.parse(project.updated || project.created) < cutoff).length;
    older.open = !!W.$("ws-project-search").value.trim() || olderOpen;
    older.append(PR.el("summary", { text: PR.t("7 天前的项目") + " · " + oldCount }));
    older.addEventListener("toggle", () => { if (older.isConnected && !W.$("ws-project-search").value.trim()) olderOpen = older.open; });
    P.items.forEach(project => {
      const row = PR.el("article", { class: "ws-project-row" });
      const mark = PR.el("span", { class: "ws-project-mark" }, PR.icon("folder"));
      const text = PR.el("div", { class: "ws-project-description" });
      const link = PR.el("a", { href: "/workspaces?" + new URLSearchParams({ project: project.id }), text: project.name });
      const title = PR.el("h2"); title.append(link);
      text.append(title, PR.el("p", { text: project.description || PR.t("尚无简介") }), PR.el("small", { text: PR.t("更新于 {time}", { time: PR.relTime(project.updated || project.created) }) }));
      const actions = PR.el("div", { class: "ws-project-actions" });
      const menu = W.iconButton(PR.t("项目操作"), () => PR.menu(menu, [
        { label: PR.t("编辑项目"), icon: "edit", fn: W.run(() => P.edit(project)) },
        { label: PR.t("导出项目"), icon: "download", fn: W.run(() => P.export(project)) },
        { label: PR.t("删除项目"), icon: "trash", fn: W.run(() => P.remove(project)) }
      ]), "more");
      actions.append(PR.el("a", { class: "btn line", href: link.href, text: PR.t("进入项目") }), menu);
      row.append(mark, text, actions);
      (Date.parse(project.updated || project.created) < cutoff ? older : list).append(row);
    });
    if (oldCount) list.append(older);
    if (!P.items.length) {
      const empty = W.empty(W.$("ws-project-search").value ? PR.t("没有匹配的项目") : PR.t("从你的第一个研究项目开始"));
      empty.append(PR.el("p", { class: "hint", text: PR.t("让论文、研究文件和原生 AI 会话汇聚一处。") }), W.button(PR.t("新建研究项目"), () => P.edit(), "plus", "accent")); list.append(empty);
    }
  };
  P.edit = async project => {
    const dialog = W.modal(project ? PR.t("编辑项目") : PR.t("新建研究项目")), form = PR.el("form");
    const name = W.field(PR.t("项目名称"), W.input(project?.name || "", { required: true, maxlength: 200 }), form);
    const description = PR.el("textarea", { class: "input", rows: 4, maxlength: 4000 }); description.value = project?.description || "";
    W.field(PR.t("项目简介"), description, form);
    let engine;
    if (!project) {
      engine = PR.el("select", { class: "input" }); engine.append(PR.el("option", { value: "codex", text: "Codex" }), PR.el("option", { value: "claude", text: "Claude Code" }));
      W.field(PR.t("初始 Agent 配置"), engine, form);
      form.append(PR.el("p", { class: "hint", text: PR.t("创建 README、Agent 说明、Git 与 Beads 工作区。之后可新建不同引擎的会话。") }));
    } else form.append(PR.el("p", { class: "hint", text: PR.t("修改显示名称不会改变项目目录：{id}", { id: project.id }) }));
    const error = PR.el("p", { class: "err", role: "alert" }), submit = PR.el("button", { class: "btn accent", type: "submit", text: PR.t("保存") });
    form.append(error, submit);
    form.onsubmit = async event => {
      event.preventDefault(); if (!name.value.trim()) return; submit.disabled = true;
      try {
        const body = { name: name.value.trim(), description: description.value.trim(), ...(!project ? { engine: engine.value } : {}) };
        const data = await W.request(project ? W.base + "/" + encodeURIComponent(project.id) + "/update" : W.base + "/create", body);
        dialog.close();
        if (!project) {
          if (!W.project) await P.refresh();
          await W.navigate("/workspaces?" + new URLSearchParams({ project: data.project.id }));
        }
        else if (W.project) { W.project = data.project; W.$("ws-project-name").textContent = data.project.name; }
        else await P.refresh();
      } catch (e) { error.textContent = e.message; } finally { submit.disabled = false; }
    };
    dialog.append(form); dialog.showModal(); name.focus();
  };
  P.export = async project => {
    if (W.project && W.files.hasDrafts()) {
      if (!await PR.confirm({ title: PR.t("导出磁盘上的项目？"), body: PR.t("尚有未保存草稿。导出包含服务器文件、Git、Beads 与会话状态，但不包含浏览器未保存的修改或主机登录凭据。"), ok: PR.t("导出") })) return;
    }
    const link = PR.el("a", { href: W.base + "/" + encodeURIComponent(project.id) + "/export", download: project.id + ".zip" }); link.click();
  };
  P.remove = async project => {
    if (!await PR.confirm({ title: PR.t("永久删除研究项目？"), body: PR.t("将删除「{name}」的全部文件、Git、Beads、会话及本地草稿。关联论文不会删除。建议先导出备份。", { name: project.name }), danger: true, ok: PR.t("删除项目") })) return;
    await W.request(W.base + "/" + encodeURIComponent(project.id) + "/delete", {});
    const prefix = "easyread-workspaces:" + W.libraryId + ":" + project.id + ":";
    try { Object.keys(localStorage).filter(key => key.startsWith(prefix)).forEach(key => localStorage.removeItem(key)); } catch (_) {}
    if (W.project) {
      W.files.buffers.clear(); W.$("ws-message").value = "";
      await W.navigate("/workspaces");
      if (PR.closeWorkspaceTab) { W.project = null; await PR.closeWorkspaceTab(); }
    } else await P.refresh();
  };
  P.init = () => {
    W.$("ws-project-new").onclick = W.run(() => P.edit());
    W.$("ws-project-search").oninput = PR.debounce(W.run(() => P.refresh()), 250);
    W.$("ws-project-import").onclick = () => W.$("ws-import-input").click();
    W.$("ws-import-input").onchange = W.run(async event => {
      const file = event.target.files[0]; event.target.value = ""; if (!file) return;
      if (!await PR.confirm({ title: PR.t("导入研究项目？"), body: PR.t("只导入可信的项目归档。导入不会运行 Agent；首次发送消息或打开终端后，Agent 将拥有完全权限。") })) return;
      W.$("ws-project-import").disabled = true;
      try {
        const data = await W.request(W.base + "/import", file, true);
        if (!W.project) await P.refresh();
        await W.navigate("/workspaces?" + new URLSearchParams({ project: data.project.id }));
      } finally { W.$("ws-project-import").disabled = false; }
    });
  };
})(window.PR, window.PR.workspaces);

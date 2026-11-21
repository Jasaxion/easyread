/* Independent entry point: navigation never restarts an Agent or native session. */
(function (PR, W) {
  "use strict";
  let viewBusy = false, showAI = false, timer;
  const visible = { files: true, papers: true };
  function sidebar() {
    W.$("ws-files-section").hidden = !visible.files;
    W.$("ws-papers-section").hidden = !visible.papers || W.view === "latex";
    const empty = !visible.files && (!visible.papers || W.view === "latex");
    W.$("ws-sidebar").hidden = empty; W.$("ws-sidebar-split").hidden = empty;
    W.$("ws-layout").classList.toggle("no-sidebar", empty);
    W.$("ws-files-toggle").setAttribute("aria-pressed", visible.files);
    W.$("ws-papers-toggle").setAttribute("aria-pressed", visible.papers);
    W.$("ws-papers-toggle").hidden = W.view === "latex";
  }
  function companion() {
    const pdf = W.view === "latex" && !showAI;
    W.$("ws-pdf-pane").hidden = !pdf; W.$("ws-chat").hidden = pdf;
    W.$("ws-companion-toggle").hidden = W.view !== "latex";
    W.$("ws-companion-toggle").textContent = pdf ? PR.t("问 AI") : PR.t("PDF 预览");
    W.$("ws-view-toggle").textContent = W.view === "latex" ? PR.t("返回研究项目") : PR.t("LaTeX 编辑器");
  }
  W.showPdf = () => { showAI = false; companion(); };
  W.switchView = async (next, updateHistory = true) => {
    if (viewBusy) return false;
    if (next === W.view) return true;
    if (!W.files.persist() || !W.sessions.persist()) return false;
    if (W.view === "latex" && !await W.latex.leave()) return false;
    viewBusy = true;
    try {
      if (next === "latex") await W.latex.enter();
      // Preserve open paths for both views; closing the pane itself still clears it.
      const previous = W.files.active?.path;
      if (!await W.files.close()) return false; if (previous) W.store("open:" + W.view, previous);
      W.view = next; W.files.selected = ""; showAI = false;
      W.$("ws-latex-toolbar").hidden = next !== "latex";
      W.$("ws-source-locate").hidden = next !== "latex"; W.$("ws-latex-import").hidden = next !== "latex";
      const fileTools = next === "latex" ? W.$("ws-file-heading-tools") : document.querySelector(".ws-file-tools");
      if (next === "latex") fileTools.append(W.$("ws-preview-toggle"), W.$("ws-goto-line"));
      else {
        fileTools.insertBefore(W.$("ws-preview-toggle"), W.$("ws-editor-settings"));
        W.$("ws-editor-settings-popover").append(W.$("ws-goto-line"));
      }
      W.$("ws-workspace").classList.toggle("ws-is-latex", next === "latex");
      document.body.classList.toggle("ws-latex-open", next === "latex"); W.papers.close();
      W.files.buffers.forEach(buffer => W.files.scheduleSave(buffer));
      sidebar(); companion();
      W.splitter(W.$("ws-editor-split"), W.$("ws-content"), next === "latex" ? "--ws-latex-source" : "--ws-editor", 52, 20, 80);
      await W.files.refresh();
      const path = PR.ls.get(W.key("open:" + next), next === "latex" ? "latex-workspace/" + W.latex.data.main : null);
      if (path) { try { await W.files.open(path); } catch (error) { if (error.status !== 404) throw error; W.error(error); } }
      if (updateHistory) {
        const url = new URL(location.href); if (next === "latex") url.searchParams.set("view", "latex"); else url.searchParams.delete("view");
        history.pushState({}, "", url);
      }
      return true;
    } finally { viewBusy = false; }
  };
  async function tick() {
    if (!W.project) return;
    if (!document.hidden && !viewBusy) {
      try {
        await Promise.all([W.files.refresh(), W.sessions.poll(), W.papers.refresh(), W.view === "latex" ? W.latex.poll() : Promise.resolve()]);
      } catch (error) {
        // Persistent, visible errors; reconnecting never resubmits writes or sends.
        W.error(error);
      }
    }
    timer = setTimeout(tick, 1800);
  }
  async function init() {
    W.projects.init();
    await W.projects.refresh();
    const query = new URLSearchParams(location.search), id = query.get("project");
    if (!id) return;
    const data = await W.request(W.base + "/" + encodeURIComponent(id)); W.project = data.project; W.workspaceRoot = data.workspace_root || "";
    W.$("ws-projects").hidden = true; W.$("ws-workspace").hidden = false; W.$("ws-back").hidden = false;
    W.$("ws-project-name").textContent = data.project.name;
    document.title = data.project.name + " · EasyRead";
    document.body.classList.add("ws-project-open"); W.$("ws-project-nav").hidden = false;
    const icons = { "ws-back": "back", "ws-files-toggle": "folder", "ws-papers-toggle": "book", "ws-project-menu": "more", "ws-file-new": "page", "ws-folder-new": "folder", "ws-upload": "upload", "ws-paper-add": "plus", "ws-session-new": "plus", "ws-session-menu": "more", "ws-send": "arrowUp", "ws-cancel": "stop", "ws-attach-file": "paperclip", "ws-latex-settings": "gear", "ws-source-locate": "search", "ws-compile-cancel": "stop",  "ws-terminal": "log", "ws-editor-settings": "gear" };
    Object.entries(icons).forEach(([id, icon]) => W.setIcon(id, icon));
    W.files.init(); W.papers.init(); W.latex.init(); W.fileLinks.init(); W.beads.init();
    await W.sessions.init();
    ["files", "papers"].forEach(name => {
      visible[name] = PR.ls.get(W.key("visible:" + name), true);
      W.$("ws-" + name + "-toggle").onclick = () => { visible[name] = !visible[name]; W.store("visible:" + name, visible[name]); sidebar(); };
      const section = W.$(name === "files" ? "ws-tree" : "ws-papers"), button = W.$("ws-" + name + "-collapse");
      section.hidden = PR.ls.get(W.key("collapsed:" + name), false); button.setAttribute("aria-expanded", !section.hidden); section.parentElement.classList.toggle("is-collapsed", section.hidden);
      button.onclick = () => { section.hidden = !section.hidden; button.setAttribute("aria-expanded", !section.hidden); section.parentElement.classList.toggle("is-collapsed", section.hidden); W.store("collapsed:" + name, section.hidden); };
    });
    sidebar(); companion();
    W.splitter(W.$("ws-sidebar-split"), W.$("ws-layout"), "--ws-sidebar", 23, 12, 48);
    W.splitter(W.$("ws-editor-split"), W.$("ws-content"), "--ws-editor", 52, 20, 80);
    W.$("ws-view-toggle").onclick = W.run(() => W.switchView(W.view === "latex" ? "project" : "latex"));
    W.$("ws-companion-toggle").onclick = () => { showAI = !showAI; companion(); };
    W.$("ws-project-menu").onclick = event => PR.menu(event.currentTarget, [
      { label: PR.t("文件草稿"), icon: "edit", fn: W.files.showDrafts },
      { label: PR.t("Git 同步"), icon: "sync", fn: W.run(W.openGit) },
      { label: PR.t("编辑项目"), icon: "edit", fn: W.run(() => W.projects.edit(W.project)) },
      { label: PR.t("导出项目"), icon: "download", fn: W.run(() => W.projects.export(W.project)) },
      { label: PR.t("运行环境"), icon: "gear", fn: () => {
        const dialog = W.modal(PR.t("运行环境")); dialog.append(PR.el("pre", { text: JSON.stringify({ capabilities: W.capabilities, project: W.project }, null, 2) })); dialog.showModal();
      } },
      { label: PR.t("删除项目"), icon: "trash", fn: W.run(() => W.projects.remove(W.project)) },
      { label: PR.t("研究探索"), icon: "search", fn: () => W.$("ws-research-link").click() },
      { label: PR.t("返回文献库"), icon: "book", fn: () => W.$("ws-library-link").click() }
    ]);
    if (query.get("view") === "latex") await W.switchView("latex", false);
    else {
      await W.files.refresh(); const path = PR.ls.get(W.key("open:project"), null); if (path) { try { await W.files.open(path); } catch (error) { if (error.status !== 404) throw error; W.error(error); } }
    }
    await W.papers.refresh();
    window.addEventListener("popstate", W.run(async () => {
      const success = await W.switchView(new URLSearchParams(location.search).get("view") === "latex" ? "latex" : "project", false);
      if (!success) {
        const url = new URL(location.href); if (W.view === "latex") url.searchParams.set("view", "latex"); else url.searchParams.delete("view");
        history.replaceState({}, "", url);
      }
    }));
    document.addEventListener("click", event => {
      const link = event.target.closest('a[href]');
      if (event.defaultPrevented) return;
      if (!link || link.target === "_blank" || link.hasAttribute("download") || !W.project) return;
      if (!W.files.persist() || !W.sessions.persist()) { event.preventDefault(); return; }
      if (W.view === "latex" && W.latex.hasDraft()) {
        event.preventDefault(); W.run(async () => { if (await W.latex.leave()) location.href = link.href; })();
      }
    });
    tick();
    window.addEventListener("pagehide", () => clearTimeout(timer));
  }
  W.run(init)();
})(window.PR, window.PR.workspaces);

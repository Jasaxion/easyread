(function (PR, W) {
  "use strict";
  const F = W.files = { buffers: new Map(), entries: [], active: null, selected: "", expanded: new Set(), editor: null, preview: false };
  let openSeq = 0, treeSignature = "", uploadTarget = "", previewSeq = 0, previewViewer;
  const clearPreview = () => { ++previewSeq; previewViewer?.destroy(); previewViewer = null; };
  const autosaveTimers = new Map(), saveRequests = new WeakMap();
  const scope = () => W.view === "latex" ? "latex-workspace" : "";
  const relative = path => scope() ? path.replace(/^latex-workspace\//, "") : path;
  const drafts = () => PR.ls.get(W.key("drafts"), {});
  F.persist = () => {
    if (F.editor && F.active) F.active.selection = F.editor.selection();
    const saved = { ...drafts() };
    F.buffers.forEach((buffer, path) => {
      if (buffer.dirty || buffer.conflict || buffer.deleted) saved[path] = buffer.draft(); else delete saved[path];
    });
    const dirty = Object.keys(saved).length > 0;
    const ok = W.store("drafts", saved);
    if (F.active) W.store("selection:" + F.active.path, F.active.selection);
    return ok || !dirty;
  };
  F.init = () => {
    W.editorSettings.init();
    F.expanded = new Set(PR.ls.get(W.key("expanded"), []));
    W.$("ws-file-new").onclick = W.run(() => F.create("file"));
    W.$("ws-folder-new").onclick = W.run(() => F.create("directory"));
    W.$("ws-upload").onclick = () => { uploadTarget = F.directory(); W.$("ws-upload-input").click(); };
    W.$("ws-upload-input").onchange = W.run(async event => {
      try { await F.upload(Array.from(event.target.files), uploadTarget); } finally { event.target.value = ""; }
    });
    W.$("ws-save").onclick = W.run(() => F.save());
    W.$("ws-close-file").onclick = W.run(() => F.close());
    W.$("ws-preview-toggle").onclick = () => { F.persist(); F.preview = !F.preview; F.renderEditor(); };
    W.$("ws-save-copy").onclick = W.run(() => F.saveCopy());
    W.$("ws-reload-file").onclick = W.run(() => F.reload());
    W.$("ws-show-remote").onclick = W.run(async () => {
      const buffer = F.active;
      const file = await W.api("/file?" + new URLSearchParams({ path: buffer.path }));
      const dialog = W.modal(PR.t("比较服务器版本")), columns = PR.el("div", { class: "ws-compare" });
      for (const [title, text] of [[PR.t("本地草稿"), buffer.content], [PR.t("服务器版本"), file.binary ? PR.t("二进制文件") : file.content]]) {
        const section = PR.el("section"); section.append(PR.el("h3", { text: title }), PR.el("pre", { text })); columns.append(section);
      }
      dialog.append(columns); dialog.showModal();
    });
    W.$("ws-autosave").checked = PR.ls.get(W.key("autosave"), true) !== false;
    W.$("ws-autosave").onchange = event => { W.store("autosave", event.target.checked); F.buffers.forEach(buffer => F.scheduleSave(buffer)); };
    W.$("ws-goto-line").onclick = W.run(async () => {
      W.editorSettings.close();
      const value = await PR.promptText({ title: PR.t("跳转到行"), value: "1" }); if (value) F.editor?.gotoLine(value);
    });
    const tree = W.$("ws-tree");
    tree.ondragover = event => { event.preventDefault(); };
    tree.ondrop = W.run(async event => {
      event.preventDefault();
      const row = event.target.closest("[data-path]");
      const entry = F.entries.find(item => item.path === row?.dataset.path);
      const dir = entry?.kind === "directory" ? entry.path : scope();
      const from = event.dataTransfer.getData("application/x-easyread-workspace-path");
      if (from) await F.move(from, [dir, from.split("/").pop()].filter(Boolean).join("/"));
      else await F.upload(Array.from(event.dataTransfer.files), dir);
    });
    window.addEventListener("beforeunload", event => {
      F.persist();
      if (F.hasDrafts() || W.sessions?.hasDraft() || W.latex?.hasDraft()) { event.preventDefault(); event.returnValue = ""; }
    });
    window.addEventListener("pagehide", F.persist);
  };
  F.showDrafts = () => {
    F.persist();
    const dialog = W.modal(PR.t("未保存的文件草稿")), saved = drafts();
    Object.keys(saved).forEach(path => dialog.append(W.button(path, async () => {
      if (W.view === "latex" && !path.startsWith("latex-workspace/")) await W.switchView("project");
      if (await F.open(path)) dialog.close();
    }, "edit", "ws-draft-link")));
    if (!Object.keys(saved).length) dialog.append(W.empty(PR.t("没有未保存的文件草稿")));
    dialog.showModal();
  };
  F.hasDrafts = () => [...F.buffers.values()].some(buffer => buffer.dirty || buffer.conflict) || Object.keys(drafts()).length > 0;
  F.directory = () => {
    const selected = F.entries.find(entry => entry.path === F.selected);
    if (selected?.kind === "directory") return selected.path;
    return F.selected.includes("/") ? F.selected.slice(0, F.selected.lastIndexOf("/")) : scope();
  };
  F.refresh = async () => {
    if (F.importing) return;
    const currentScope = scope();
    const tree = await W.api("/tree?" + new URLSearchParams({ path: currentScope }));
    if (scope() !== currentScope) return;
    const entries = new Map((tree.entries || []).map(entry => [entry.path, entry]));
    if (!currentScope) {
      for (const path of F.expanded) {
        if (!/^(\.git|\.beads)(\/|$)/.test(path)) continue;
        try {
          const subtree = await W.api("/tree?" + new URLSearchParams({ path }));
          (subtree.entries || []).forEach(entry => entries.set(entry.path, entry));
        } catch (error) { if (error.status !== 404) throw error; F.expanded.delete(path); }
      }
    }
    if (scope() !== currentScope) return;
    F.entries = [...entries.values()];
    const signature = JSON.stringify(F.entries);
    if (signature !== treeSignature) { treeSignature = signature; F.renderTree(); W.latex?.updateFiles(); }
    const buffer = F.active;
    if (!buffer || buffer.saving) return;
    try {
      const file = await W.api("/file?" + new URLSearchParams({ path: buffer.path }));
      if (F.active !== buffer) return;
      if (buffer.observe(file)) {
        W.latex?.sourceChanged(buffer.path);
        if (F.editor && !buffer.binary) F.editor.replace(buffer.content); else F.renderEditor();
        if (F.preview) F.renderPreview();
      }
      F.status(); F.persist();
    } catch (error) {
      if (error.status !== 404) throw error;
      buffer.deleted = true; buffer.conflict = true; F.status(); F.persist();
    }
  };
  F.renderTree = () => {
    const tree = W.$("ws-tree"), focused = document.activeElement?.closest("[data-path]")?.dataset.path;
    tree.replaceChildren();
    const all = F.entries.filter(entry => entry.path !== scope()).sort((a, b) => {
      const aa = a.path.split("/"), bb = b.path.split("/");
      for (let i = 0; i < Math.min(aa.length, bb.length); i++) {
        if (aa[i] !== bb[i]) {
          const ad = i < aa.length - 1 || a.kind === "directory", bd = i < bb.length - 1 || b.kind === "directory";
          return ad !== bd ? ad ? -1 : 1 : aa[i].localeCompare(bb[i]);
        }
      }
      return aa.length - bb.length;
    });
    all.forEach(entry => {
      const parts = entry.path.split("/");
      if (parts.slice(0, -1).some((_, i) => { const p = parts.slice(0, i + 1).join("/"); return p !== scope() && !F.expanded.has(p); })) return;
      const row = PR.el("div", { class: "ws-file-row" + (entry.path === F.selected ? " on" : ""), "data-path": entry.path });
      row.style.paddingInlineStart = Math.max(0, relative(entry.path).split("/").length - 1) * 14 + "px";
      const dir = entry.kind === "directory";
      const button = W.button(entry.name || parts.at(-1), async () => {
        F.selected = entry.path;
        if (dir) {
          if (F.expanded.has(entry.path)) F.expanded.delete(entry.path);
          else {
            F.expanded.add(entry.path);
            // Servers may bound Git/Beads internals; expanding requests the lazy subtree.
            if (/^(\.git|\.beads)(\/|$)/.test(entry.path)) {
              const subtree = await W.api("/tree?" + new URLSearchParams({ path: entry.path }));
              const byPath = new Map(F.entries.map(e => [e.path, e]));
              (subtree.entries || []).forEach(e => byPath.set(e.path, e)); F.entries = [...byPath.values()];
            }
          }
          W.store("expanded", [...F.expanded]); F.renderTree();
        } else if (entry.kind === "symlink") W.error(new Error(PR.t("符号链接不可打开，以免访问工作区之外的文件。")));
        else await F.open(entry.path);
      }, dir ? "folder" : "page");
      button.classList.add("ws-file-name"); button.title = entry.path;
      if (dir) { button.setAttribute("aria-expanded", F.expanded.has(entry.path)); button.prepend(document.createTextNode(F.expanded.has(entry.path) ? "▾ " : "▸ ")); }
      const more = W.button(PR.t("文件操作"), () => F.menu(entry, more), "more", "ws-file-more");
      row.append(button, more); row.draggable = true;
      row.ondragstart = event => event.dataTransfer.setData("application/x-easyread-workspace-path", entry.path);
      row.oncontextmenu = event => { event.preventDefault(); F.menu(entry, event); };
      tree.append(row);
    });
    if (!tree.children.length) tree.append(W.empty(PR.t("此目录还没有文件")));
    if (focused) [...tree.querySelectorAll("[data-path]")].find(row => row.dataset.path === focused)?.querySelector("button")?.focus({ preventScroll: true });
  };
  F.menu = (entry, where) => PR.menu(where, [
    { label: PR.t("重命名 / 移动"), icon: "edit", fn: W.run(() => F.move(entry.path)) },
    ...(entry.kind === "file" ? [{ label: PR.t("下载"), icon: "download", fn: () => { const link = PR.el("a", { href: W.raw(entry.path), download: entry.name }); link.click(); } }] : []),
    { label: PR.t("删除"), icon: "trash", fn: W.run(() => F.remove(entry.path)) }
  ]);
  F.open = async (path, line) => {
    if (F.importing || !F.persist()) return false;
    const seq = ++openSeq;
    let buffer = F.buffers.get(path);
    let file, deleted = false;
    try { file = await W.api("/file?" + new URLSearchParams({ path })); }
    catch (error) {
      const draft = drafts()[path] || buffer?.draft();
      if (error.status !== 404 || !draft) throw error;
      file = { path, content: draft.base, version: draft.version, binary: false }; deleted = true;
    }
    if (seq !== openSeq) return false;
    if (buffer) { if (buffer.observe(file)) W.latex?.sourceChanged(path); }
    else { buffer = new PR.WorkspaceBuffer(file, drafts()[path]); F.buffers.set(path, buffer); }
    if (deleted) { buffer.deleted = true; buffer.conflict = true; }
    F.active = buffer; F.selected = path; F.preview = false;
    path.split("/").slice(0, -1).forEach((_, index, parts) => F.expanded.add(parts.slice(0, index + 1).join("/")));
    W.store("expanded", [...F.expanded]); W.store("open:" + W.view, path);
    F.renderTree(); F.renderEditor();
    if (line) F.editor?.gotoLine(line);
    F.scheduleSave(buffer); W.latex?.diagnostics(); return true;
  };
  F.close = async () => {
    if (!F.persist()) return false;
    ++openSeq; W.editorSettings.close(); clearPreview(); F.editor?.destroy(); F.editor = null; F.active = null;
    W.$("ws-file-pane").hidden = true; W.$("ws-editor-split").hidden = true;
    W.$("ws-content").classList.remove("has-file"); W.store("open:" + W.view, null); return true;
  };
  F.renderEditor = () => {
    const buffer = F.active; if (!buffer) return;
    W.editorSettings.close();
    W.$("ws-editor-settings").hidden = buffer.binary;
    W.$("ws-goto-line").disabled = buffer.binary || F.preview;
    clearPreview();
    if (F.editor) { F.editor.destroy(); F.editor = null; }
    W.$("ws-file-pane").hidden = false; W.$("ws-editor-split").hidden = false; W.$("ws-content").classList.add("has-file");
    W.$("ws-file-title").textContent = relative(buffer.path);
    W.$("ws-editor").replaceChildren();
    W.$("ws-preview-toggle").hidden = buffer.binary || !/\.(md|markdown)$/i.test(buffer.path);
    W.$("ws-editor").hidden = buffer.binary || F.preview;
    W.$("ws-file-preview").hidden = !buffer.binary && !F.preview;
    if (!buffer.binary && !F.preview) {
      F.editor = PR.workspaceEditor.create(W.$("ws-editor"), { path: buffer.path, content: buffer.content,
        fontSize: W.view === "latex" ? W.latex.fontSize() : W.editorSettings.fontSize(),
        onLocate: W.view === "latex" ? W.run(() => W.latex.forwardSync()) : undefined,
        files: () => F.entries.filter(e => e.kind === "file").map(e => e.path.replace(/^latex-workspace\//, "")),
        onChange: content => { buffer.content = content; F.persist(); F.status(); F.scheduleSave(buffer); W.latex?.sourceChanged(buffer.path); },
        onSelection: selection => { buffer.selection = { anchor: selection.anchor, head: selection.head }; },
        onCompile: W.view === "latex" ? () => W.$("ws-compile").click() : undefined,
        onSave: W.run(() => F.save()) });
      F.editor.restore(buffer.selection || PR.ls.get(W.key("selection:" + buffer.path), null));
    } else F.renderPreview();
    F.status();
  };
  F.renderPreview = () => {
    const buffer = F.active, box = W.$("ws-file-preview"); clearPreview(); box.replaceChildren(); box.classList.remove("ws-file-preview-pdf");
    if (!buffer.binary) { box.innerHTML = PR.mdBlocks(buffer.content, W.fileLinks.options(buffer.path)); return; }
    if (/^image\/(png|jpeg|gif|webp|bmp)$/.test(buffer.mime)) box.append(PR.el("img", { src: W.raw(buffer.path, buffer.version), alt: buffer.path }));
    else if (buffer.mime === "application/pdf") {
      const seq = previewSeq, controls = PR.el("div", { class: "ws-pdf-heading" }), label = PR.el("span", { class: "hint", text: PR.t("正在载入 PDF…") });
      const pages = PR.el("div", { class: "ws-pdf-viewer", tabindex: "0", "aria-label": buffer.path });
      let page = 1, zoom = 100;
      controls.append(W.button(PR.t("上一页"), () => previewViewer?.gotoPage(page - 1), "back"), label,
        W.button(PR.t("下一页"), () => previewViewer?.gotoPage(page + 1)),
        W.button(PR.t("缩小"), () => previewViewer?.setZoom(zoom - 10)), W.button(PR.t("放大"), () => previewViewer?.setZoom(zoom + 10)),
        W.button(PR.t("适合宽度"), () => previewViewer?.fitWidth()));
      box.classList.add("ws-file-preview-pdf"); box.append(controls, pages);
      import("/web/vendor/workspace-pdf.js").then(async module => {
        if (seq !== previewSeq) return;
        const instance = previewViewer = module.create(pages, { onChange: state => {
          page = state.page; zoom = state.zoom; label.textContent = page + " / " + state.pages + " · " + zoom + "%";
        }, onError: error => { label.textContent = error.message; } });
        await instance.load(W.raw(buffer.path, buffer.version));
      }).catch(error => { if (seq === previewSeq) label.textContent = PR.t("PDF 载入失败：{error}", { error: error.message }); });
    }
    else box.append(W.empty(PR.t("此文件不支持在线预览。")));
    box.append(PR.el("a", { class: "btn line", href: W.raw(buffer.path), download: buffer.path.split("/").pop(), text: PR.t("下载文件") }));
  };
  F.status = () => {
    const buffer = F.active; if (!buffer) return;
    W.$("ws-file-status").textContent = buffer.saving ? PR.t("保存中…") : buffer.conflict ? PR.t("文件有冲突") : buffer.dirty ? PR.t("未保存") : PR.t("已保存");
    W.$("ws-save").disabled = !buffer.dirty || buffer.saving || buffer.conflict || buffer.binary;
    W.$("ws-conflict").hidden = !buffer.conflict;
    W.$("ws-conflict-text").textContent = buffer.deleted ? PR.t("文件已在服务器删除。草稿仍保留，可另存为新文件。") : PR.t("服务器文件已更改。你的草稿没有被覆盖；请比较服务器版本、另存副本或明确重新载入。");
    W.$("ws-show-remote").disabled = buffer.deleted;
    W.$("ws-reload-file").disabled = buffer.deleted;
  };
  F.autosaveEnabled = (buffer = F.active) => W.view === "latex" || !!buffer?.path.startsWith("latex-workspace/") || W.$("ws-autosave").checked;
  F.flush = async (prefix = "") => {
    if (!F.persist()) return false;
    const paths = [...new Set([...F.buffers.keys(), ...Object.keys(drafts())])].filter(path => path.startsWith(prefix));
    for (const path of paths) {
      let buffer = F.buffers.get(path);
      if (!buffer) { buffer = new PR.WorkspaceBuffer(await W.api("/file?" + new URLSearchParams({ path })), drafts()[path]); F.buffers.set(path, buffer); }
      if (buffer.conflict || buffer.deleted) return false;
      do { if (!await F.save(buffer)) return false; } while (buffer.dirty);
    }
    return true;
  };
  F.scheduleSave = (buffer = F.active) => {
    clearTimeout(autosaveTimers.get(buffer)); autosaveTimers.delete(buffer);
    // Timers belong to buffers, not the visible editor: switching/closing cannot strand a save.
    if (!F.importing && F.autosaveEnabled(buffer) && buffer?.dirty && !buffer.conflict && !buffer.deleted) {
      autosaveTimers.set(buffer, setTimeout(W.run(async () => {
        autosaveTimers.delete(buffer);
        if (F.buffers.get(buffer.path) === buffer && !buffer.saving) await F.save(buffer);
      }), 1200));
    }
  };
  F.save = async (buffer = F.active) => {
    if (!buffer) return true;
    // Manual save / compile joins autosave instead of reporting a false conflict.
    if (saveRequests.has(buffer)) {
      const saved = await saveRequests.get(buffer);
      return saved && buffer.dirty ? F.save(buffer) : saved;
    }
    const pending = buffer.save(body => W.api("/files/write", body));
    saveRequests.set(buffer, pending); F.status();
    try {
      const saved = await pending;
      if (saved && buffer.dirty) F.scheduleSave(buffer);
      return saved;
    } finally { saveRequests.delete(buffer); F.status(); F.persist(); }
  };
  F.saveCopy = async () => {
    const buffer = F.active; if (!buffer) return;
    const path = await PR.promptText({ title: PR.t("另存副本"), value: buffer.path + ".copy", max: 1000 }); if (!path) return;
    await W.api("/files/create", { path: W.safePath(path, scope()), kind: "file", content: buffer.content });
    await F.refresh(); PR.toast(PR.t("副本已保存；原草稿仍保留。"));
  };
  F.reload = async () => {
    const buffer = F.active;
    if (!await PR.confirm({ title: PR.t("重新载入服务器版本？"), body: PR.t("这会丢弃此文件的本地草稿。可先另存副本。"), danger: true })) return;
    const file = await W.api("/file?" + new URLSearchParams({ path: buffer.path }));
    clearTimeout(autosaveTimers.get(buffer)); autosaveTimers.delete(buffer);
    F.buffers.delete(buffer.path); const stored = drafts(); delete stored[buffer.path]; W.store("drafts", stored);
    const replacement = new PR.WorkspaceBuffer(file); F.buffers.set(file.path, replacement);
    if (F.active === buffer) { F.active = replacement; F.renderEditor(); }
  };
  F.create = async kind => {
    const dir = F.directory();
    const path = await PR.promptText({ title: kind === "file" ? PR.t("新建文件") : PR.t("新建文件夹"), value: dir ? dir + "/" : "", max: 1000 }); if (!path) return;
    const valid = W.safePath(path, scope());
    await W.api("/files/create", { path: valid, kind, ...(kind === "file" ? { content: "" } : {}) });
    valid.split("/").slice(0, -1).forEach((_, i, parts) => F.expanded.add(parts.slice(0, i + 1).join("/")));
    await F.refresh(); if (kind === "file") await F.open(valid);
  };
  F.upload = async (files, dir) => {
    if (F.importing) return;
    for (const file of files) {
      if (W.view === "latex" && /\.zip$/i.test(file.name)) await W.latex.importZip(file);
      else await W.api("/files/upload?" + new URLSearchParams({ path: W.safePath([dir, file.name].filter(Boolean).join("/"), scope()) }), file, true);
    }
    await F.refresh();
  };
  F.affected = path => [...new Set([...F.buffers.keys(), ...Object.keys(drafts())])].filter(p => p === path || p.startsWith(path + "/"));
  F.move = async (path, destination) => {
    if (!F.persist()) return;
    destination = destination || await PR.promptText({ title: PR.t("重命名 / 移动"), value: path, max: 1000 });
    if (!destination || destination === path) return;
    W.safePath(destination, scope());
    if (!await PR.confirm({ title: PR.t("移动文件？"), body: path + " → " + destination })) return;
    await W.api("/files/move", { path, destination });
    const stored = drafts();
    F.affected(path).forEach(old => {
      const next = destination + old.slice(path.length), buffer = F.buffers.get(old);
      if (stored[old]) { stored[next] = stored[old]; delete stored[old]; }
      if (buffer) { F.buffers.delete(old); buffer.path = next; F.buffers.set(next, buffer); }
    });
    W.store("drafts", stored); F.selected = destination;
    if (F.active) { W.store("open:" + W.view, F.active.path); F.renderEditor(); }
    treeSignature = ""; await F.refresh();
  };
  F.remove = async path => {
    if (!F.persist()) return;
    if (!await PR.confirm({ title: PR.t("永久删除文件？"), body: PR.t("将删除 {path} 及其子文件，包括这些文件的本地草稿。此操作不可撤销。", { path }), danger: true, ok: PR.t("删除") })) return;
    await W.api("/files/delete", { path });
    const affected = F.affected(path), stored = drafts();
    if (F.active && affected.includes(F.active.path)) await F.close();
    affected.forEach(p => { F.buffers.delete(p); delete stored[p]; }); W.store("drafts", stored);
    F.selected = ""; await F.refresh();
  };
})(window.PR, window.PR.workspaces);

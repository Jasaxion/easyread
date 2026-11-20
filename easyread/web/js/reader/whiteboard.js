/* Lightweight dock controller. No scene allocation or editor assets before explicit creation. */
(function (PR) {
  "use strict";
  const MIME = "application/x-easyread-whiteboard-source", drafts = globalThis.EasyReadWhiteboardDrafts;
  let panel, frame, editor, sync, scope, previewTimer, epoch = 0, resetVersion = 0, loading = false, loaded = false, deleting = false, expanded = false, recovery = [], queued = [];
  const uuid = () => PR.uid("wb") + "-" + Math.random().toString(36).slice(2);
  const theme = () => document.documentElement.dataset.theme === "dark" ? "dark" : "light";
  const endpoint = () => "/api/p/" + encodeURIComponent(PR.pid) + "/whiteboard";
  const $ = selector => panel.querySelector(selector);
  async function request(body) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(endpoint(), { cache: "no-store", signal: controller.signal,
        ...(body ? { method: "POST", headers: { "Content-Type": "application/json", "X-Token": PR.token || "" }, body: JSON.stringify({ ...body, library_id: scope?.split(":")[0] }) } : {}) });
      if (!response.ok || response.redirected) {
        const error = new Error(PR.t("请求失败 {status}", { status: response.status })); error.status = response.status; throw error;
      }
      return await response.json();
    } finally { clearTimeout(timer); }
  }
  function message(text) { $(".wb-message").textContent = text; }
  function status(state, detail) {
    const labels = { pending: PR.t("白板待保存"), saving: PR.t("正在保存白板…"), saved: PR.t("白板已保存"),
      error: PR.t("白板未同步，请保持页面打开并重试。"), conflict: PR.t("白板已在其他页面更新。请导出本地备份，再重新载入。"),
      "storage-error": PR.t("本地草稿写入失败，请勿关闭页面；请导出备份。") };
    $(".wb-status").textContent = detail.localError ? labels["storage-error"] : labels[state];
    $("[data-wb=retry]").hidden = !["error", "pending", "storage-error"].includes(state);
    $("[data-wb=reload]").hidden = state !== "conflict";
  }
  function download(scene, name = "whiteboard") {
    const blob = new Blob([JSON.stringify(scene, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob), a = document.createElement("a");
    a.href = url; a.download = name + ".excalidraw"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
  }
  function fullscreen(on) {
    expanded = on;
    panel.classList.toggle("wb-expanded", on);
    document.body.classList.toggle("wb-fullscreen", on);
    $("[data-wb=expand]").textContent = on ? PR.t("退出全屏") : PR.t("全屏");
    $("[data-wb=expand]").setAttribute("aria-pressed", String(on));
  }
  function openSource(link) {
    if (!link) return;
    let url; try { url = new URL(link, location.href); } catch { return; }
    if (url.origin === location.origin && url.pathname === "/read/" + PR.pid && url.hash.startsWith("#b-")) {
      let id; try { id = decodeURIComponent(url.hash.slice(1)); } catch { return; }
      if (!PR.blockById[id.slice(2)]) return;
      fullscreen(false);
      if (innerWidth <= 760) PR.openSide(null);
      PR.jumpTo(id); return;
    }
    if (["https:", "http:"].includes(url.protocol) && !url.username) window.open(url.href, "_blank", "noopener,noreferrer");
  }
  function source(ref) {
    if (!ref || ref.paper !== PR.pid || typeof ref.anchor !== "string") return null;
    const block = PR.blockById[ref.anchor];
    if (!block && ref.anchor !== "head") return null;
    let text = "", kind = ref.kind || "quote";
    if (kind === "note") {
      const note = (PR.state.reader.notes || {})[ref.note];
      if (!note || (note.anchor || "head") !== ref.anchor) return null;
      text = [note.quote, note.body].filter(Boolean).join("\n\n");
    } else if (kind === "paper-note") text = PR.paperNoteEditor?.getBody() ?? PR.state.reader.paper_note?.body ?? "";
    else text = typeof ref.text === "string" ? ref.text : PR.plain(PR.textFor(ref.anchor) || block?.zh || block?.en || block?.tex || "");
    if (!text.trim()) return null;
    const title = PR.state.paper.meta.title_zh || PR.state.paper.meta.title_en || PR.t("论文");
    return { text: text.slice(0, 12000), anchor: ref.anchor, kind,
      label: title + (block?.page ? " · p." + block.page : ""),
      link: "/read/" + encodeURIComponent(PR.pid) + (block ? "#b-" + encodeURIComponent(ref.anchor) : "") };
  }
  function noBoard() {
    PR.toast(PR.t("当前文档没有白板，请先在侧栏创建白板！"), { label: PR.t("打开白板"), fn: () => PR.openSide("whiteboard") }, 6000);
  }
  async function insertRefs(refs) {
    if (deleting || PR.store.mode !== "server" || !PR.feature("whiteboard")) return;
    const reset = resetVersion;
    refs = refs.filter(ref => source(ref));
    if (!refs.length) return;
    PR.hideBlockbar?.();
    try {
      if (!sync) {
        const data = await request();
        if (PR.state.library_id && data.library_id !== PR.state.library_id) throw new Error("Library identity changed");
        if (!data.board) { noBoard(); return; }
      }
      if (deleting || reset !== resetVersion) return;
      queued.push(...refs);
      PR.openSide("whiteboard");
      if (sync) drain();
      else if (!loading) load();
    } catch (error) { PR.toast(PR.t("白板操作失败：{error}", { error: PR.esc(PR.errorText(error)) })); }
  }
  function drain() { if (editor) { queued.splice(0).forEach(ref => { const value = source(ref); if (value) editor.insert(value); }); } }
  function removeFrame() { epoch++; clearTimeout(previewTimer); editor = null; const old = frame; frame = null; old?.remove(); }
  function preview(scene, revision) {
    clearTimeout(previewTimer);
    const owner = sync, renderer = editor;
    const current = () => sync === owner && editor === renderer && !deleting && !owner.dirty && owner.revision === revision;
    if (!owner || !renderer?.preview || !current()) return;
    previewTimer = setTimeout(async () => {
      if (!current()) return;
      try {
        const blob = await renderer.preview(scene);
        if (!current()) return;
        const image = await new Promise((resolve, reject) => {
          const reader = new FileReader(); reader.onerror = () => reject(reader.error);
          reader.onload = () => resolve(String(reader.result).split(",")[1]); reader.readAsDataURL(blob);
        });
        if (!current()) return;
        await request({ action: "preview", revision, image });
        if (current()) $(".wb-preview-status").textContent = "";
      } catch (error) {
        if (current() && error.status !== 409) $(".wb-preview-status").textContent = PR.t("预览图暂未更新，白板内容不受影响。");
      }
    }, 1500);
  }
  function mount() {
    if (!sync) return;
    $(".wb-empty").hidden = true; $(".wb-tools").hidden = false;
    message(PR.t("正在加载白板…"));
    if (!frame) {
      frame = document.createElement("iframe"); frame.className = "wb-frame";
      frame.title = PR.t("白板编辑器"); frame.allow = "clipboard-read; clipboard-write";
      frame.addEventListener("load", async event => {
        if (event.target !== frame || !sync) return;
        const generation = ++epoch, owner = sync;
        editor = null;
        try {
          const bridge = frame.contentWindow.EasyReadWhiteboardEditor;
          if (!bridge) throw new Error("Missing editor assets");
          await bridge.mount({ scene: owner.scene, theme: theme(), lang: PR.lang === "en" ? "en" : PR.lang === "zh-Hant" ? "zh-TW" : "zh-CN",
            paper: PR.pid, name: PR.state.paper.meta.title_zh || PR.state.paper.meta.title_en || "EasyRead",
            onChange: scene => { if (generation === epoch && sync === owner) owner.change(scene); }, onLink: openSource, resolveSource: source,
            onEscape: () => { if (expanded) fullscreen(false); } });
          if (generation !== epoch || sync !== owner) return;
          editor = bridge; message(""); drain(); preview(owner.scene, owner.revision);
        } catch (error) { message(PR.t("白板编辑器加载失败，请重试。")); $("[data-wb=editor-retry]").hidden = false; }
      });
      frame.src = "/web/whiteboard.html"; $(".wb-canvas").append(frame);
    }
  }
  function useBoard(board) {
    sync = globalThis.EasyReadWhiteboardSync.create({ id: uuid(), scope, revision: board.revision, scene: board.scene, drafts, uuid, status,
      save: snapshot => request({ action: "save", ...snapshot }), onSaved: preview });
    status("saved", {}); mount();
  }
  function showRecovery() {
    const box = $(".wb-recovery"); box.replaceChildren(); box.hidden = !recovery.length;
    if (!recovery.length) return;
    const text = document.createElement("p"); text.textContent = PR.t("发现未同步的白板草稿，请选择恢复或导出备份。");
    const select = document.createElement("select"); select.setAttribute("aria-label", PR.t("白板本地草稿"));
    recovery.forEach((row, index) => { const option = document.createElement("option"); option.value = index; option.textContent = new Date(row.at).toLocaleString(); select.append(option); });
    box.append(text, select);
    for (const [action, label] of [["recover", PR.t("恢复草稿")], ["draft-export", PR.t("导出草稿")], ["draft-delete", PR.t("删除草稿")]]) {
      const button = document.createElement("button"); button.className = "btn sm"; button.dataset.wb = action; button.textContent = label; box.append(button);
    }
  }
  async function load() {
    if (loading) return;
    if (PR.store.mode !== "server") {
      message(PR.t("白板编辑请在 EasyRead 服务中打开；独立 HTML 不包含白板，请提前导出 .excalidraw 备份。"));
      $(".wb-empty").hidden = true; return;
    }
    loading = true; message(PR.t("正在读取白板…")); $("[data-wb=create]").disabled = true;
    try {
      const data = await request();
      if (PR.state.library_id && data.library_id !== PR.state.library_id) throw new Error("Library identity changed");
      scope = data.library_id + ":" + PR.pid;
      loaded = true; message(""); $("[data-wb=load]").hidden = true;
      if (data.board) useBoard(data.board);
      else {
        $(".wb-empty").hidden = false; $("[data-wb=create]").disabled = false;
        if (queued.length) { queued = []; noBoard(); }
      }
      // Recovery is offered only after an authorized read of this library.
      try { recovery = await drafts.list(scope); showRecovery(); }
      catch { message(PR.t("本地草稿不可用；请保持连接并定期导出备份。")); }
    } catch (error) {
      message(PR.t("无法读取白板，请检查连接后重试。")); $("[data-wb=load]").hidden = false;
    } finally { loading = false; }
  }
  async function deleteBoard() {
    const owner = sync;
    if (!owner || deleting || !await PR.confirm({ title: PR.t("删除白板？"),
      body: PR.t("将删除当前文档的白板、预览图及本机草稿，不会删除正文和笔记。此操作无法撤销，建议先导出备份。"),
      ok: PR.t("删除白板"), danger: true })) return;
    deleting = true; $("[data-wb=delete]").disabled = true;
    try {
      editor?.flush(); if (frame) frame.inert = true;
      clearTimeout(previewTimer); await owner.pause();
      await request({ action: "delete", revision: owner.revision });
      resetVersion++; removeFrame(); sync = null; queued = []; fullscreen(false);
      recovery = []; showRecovery(); message("");
      $(".wb-tools").hidden = true; $(".wb-empty").hidden = false; $("[data-wb=create]").disabled = false;
      $(".wb-status").textContent = ""; $(".wb-preview-status").textContent = "";
      for (const key of ["retry", "reload", "editor-retry"]) $("[data-wb=" + key + "]").hidden = true;
      try {
        await owner.discard();
        for (const draft of await drafts.list(scope)) await drafts.remove(draft.id);
      } catch { message(PR.t("白板已删除，但本地草稿未能清理。")); }
      PR.toast(PR.t("白板已删除"));
    } catch (error) {
      if (sync === owner) owner.resume();
      throw error;
    } finally { deleting = false; $("[data-wb=delete]").disabled = false; if (frame) frame.inert = false; }
  }
  async function action(kind) {
    if (deleting) return;
    try {
      if (kind === "delete") return await deleteBoard();
      if (kind === "load") return load();
      if (kind === "create") {
        if (!loaded || loading) return;
        loading = true; $("[data-wb=create]").disabled = true;
        try { useBoard((await request({ action: "create" })).board); }
        finally { loading = false; $("[data-wb=create]").disabled = false; }
      }
      if (kind === "expand") fullscreen(!expanded);
      if (kind === "close") { fullscreen(false); PR.closeDockTab("whiteboard"); }
      if (kind === "backup") { editor?.flush(); download(sync.scene, PR.pid + "-whiteboard"); }
      if (kind === "retry") { editor?.flush(); await sync.flush(); }
      if (kind === "editor-retry") { $("[data-wb=editor-retry]").hidden = true; removeFrame(); mount(); }
      if (kind === "reload") {
        if (!confirm(PR.t("重新载入会放弃当前未同步的修改，请先导出备份。继续？"))) return;
        if (sync.busy) return;
        removeFrame(); await sync.discard(); sync = null; await load();
      }
      if (["recover", "draft-export", "draft-delete"].includes(kind)) {
        const record = recovery[Number($(".wb-recovery select").value)]; if (!record) return;
        if (kind === "draft-export") return download(record.pending.scene, PR.pid + "-draft");
        if (kind === "draft-delete") {
          if (!confirm(PR.t("确定删除这份本地白板草稿？"))) return;
          await drafts.remove(record.id); recovery = recovery.filter(row => row !== record); showRecovery(); return;
        }
        if (!sync) { message(PR.t("请先创建白板，再恢复草稿。")); return; }
        if (sync.dirty || sync.busy) { message(PR.t("请先保存或导出当前修改，再恢复草稿。")); return; }
        removeFrame(); sync.restore(record); mount(); await sync.flush();
        recovery = recovery.filter(row => row !== record); showRecovery();
      }
    } catch (error) { message(PR.t("白板操作失败：{error}", { error: PR.errorText(error) })); }
  }
  PR.whiteboard = {
    show(node) {
      if (panel) return;
      panel = node; panel.className = "dock-resource wb-panel";
      panel.setAttribute("aria-label", PR.t("白板"));
      panel.innerHTML = '<div class="wb-tools" hidden><button class="btn sm" data-wb="backup">' + PR.t("导出备份") + '</button><button class="btn sm danger" data-wb="delete">' + PR.t("删除白板") + '</button><span class="grow"></span><button class="btn sm" data-wb="expand" aria-pressed="false">' + PR.t("全屏") + '</button><button class="btn sm" data-wb="close">' + PR.t("关闭") + '</button></div>' +
        '<div class="wb-empty"><p>' + PR.t("这篇文档还没有白板") + '</p><button class="btn accent" data-wb="create" disabled>' + PR.t("创建白板") + '</button><p class="hint">' + PR.t("创建后可以绘图、导入图片，并拖入正文选文或笔记。") + '</p></div>' +
        '<p class="wb-message" role="status"></p><button class="btn sm" data-wb="load" hidden>' + PR.t("重试") + '</button><button class="btn sm" data-wb="editor-retry" hidden>' + PR.t("重试加载编辑器") + '</button><div class="wb-recovery" hidden></div><div class="wb-canvas"></div><div class="wb-save"><span class="wb-status" role="status"></span><span class="wb-preview-status" role="status"></span><button class="btn sm" data-wb="retry" hidden>' + PR.t("重试保存") + '</button><button class="btn sm" data-wb="reload" hidden>' + PR.t("重新载入") + '</button></div>';
      panel.addEventListener("click", e => { const b = e.target.closest("[data-wb]"); if (b) action(b.dataset.wb); });
      panel.addEventListener("keydown", e => {
        if (e.key === "Escape" && expanded) { e.preventDefault(); e.stopPropagation(); fullscreen(false); }
      }, true);
      load();
    },
    async prepareLogout() { editor?.flush(); await sync?.flush(); if (sync?.dirty) throw new Error(PR.t("仍有未同步修改，请先联网保存或导出后再退出。")); },
    beforeMove() { editor?.flush(); },
    hide() { if (!panel) return; fullscreen(false); editor?.flush(); sync?.flush(); },
    insertBlock(id) {
      const b = PR.blockById[id]; if (!b) return;
      const original = [b.en, b.caption_en, b.image_en, ...(b.items || []).map(item => item.en)].filter(Boolean).join("\n");
      const translated = PR.blockKeys(b).map(PR.textFor).filter(Boolean).join("\n") || b.tex || "";
      const view = PR.readingView();
      const parts = view === "original" ? [original || translated] : view === "both"
        ? (PR.originalFirst() ? [original, translated] : [translated, original]) : [translated || original];
      return insertRefs([{ paper: PR.pid, anchor: id, text: PR.plain([...new Set(parts.filter(Boolean))].join("\n\n")) }]);
    },
    quote(parts) {
      return insertRefs(parts.map(part => ({ paper: PR.pid, anchor: part.anchor, text: part.quote })));
    },
  };
  new MutationObserver(() => editor?.theme(theme())).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-whiteboard-note]"); if (!b) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const card = b.closest("[data-note]");
    void insertRefs([{ paper: PR.pid, anchor: card.dataset.anchor || "head", kind: "note", note: card.dataset.note }]);
  }, true);
  document.addEventListener("dragstart", e => {
    if (!e.dataTransfer) return;
    const b = e.target.closest?.("[data-whiteboard-note]");
    const block = e.target.closest?.('#paper .blk[id^="b-"]');
    let ref;
    if (b) { const card = b.closest("[data-note]"); ref = { paper: PR.pid, anchor: card.dataset.anchor || "head", kind: "note", note: card.dataset.note }; }
    else if (block && getSelection().toString().trim()) ref = { paper: PR.pid, anchor: block.id.slice(2), text: getSelection().toString() };
    if (ref) { e.dataTransfer.setData(MIME, JSON.stringify(ref)); e.dataTransfer.effectAllowed = "copy"; }
  }, true);
  window.addEventListener("online", () => sync?.flush());
  document.addEventListener("visibilitychange", () => { if (document.hidden) { editor?.flush(); sync?.flush(); } });
  window.addEventListener("beforeunload", e => { editor?.flush(); if (sync?.dirty) { sync.flush(); e.preventDefault(); e.returnValue = ""; } });
})(window.PR);

(function (PR, W) {
  "use strict";
  const L = W.latex = { data: null, importing: false };
  let signature = "", pdfVersion = "", settingsDirty = false, settingsPending = null, compiling = false;
  let viewer, viewerPromise, pdfPath = "", pdfSequence = 0, pdfLoading = false, showLog = false, syncSequence = 0;
  let sourceRevision = 0, compilationRevision = null, sourceStale = false;
  let stateSequence = 0, stateVersion = "", stateKey = "";
  const invalidateState = () => { ++stateSequence; stateVersion = ""; };
  const sourcePath = path => path.startsWith("latex-workspace/") ? path : "latex-workspace/" + path.replace(/^\.\//, "");
  L.hasDraft = () => settingsDirty;
  L.fontSize = () => Math.max(10, Math.min(22, Number(PR.ls.get(W.key("latex-ui"), {}).fontSize) || 12));
  L.syncStatus = text => { W.$("ws-sync-status").textContent = text || ""; W.$("ws-sync-status").hidden = !text; };
  L.sourceChanged = path => {
    if (path.startsWith("latex-workspace/")) {
      ++syncSequence; ++sourceRevision;
      if (pdfPath) { sourceStale = true; L.syncStatus(PR.t("源码已更改；请重新编译后定位。")); }
    }
  };
  L.showLog = value => {
    showLog = value;
    W.$("ws-latex-log").hidden = !showLog;
    W.$("ws-pdf-controls").hidden = showLog;
    W.$("ws-pdf").hidden = showLog || !pdfPath || pdfLoading;
    W.$("ws-pdf-empty").hidden = showLog || (!!pdfPath && !pdfLoading);
    W.$("ws-latex-log-toggle").textContent = showLog ? PR.t("PDF 预览") : PR.t("编译日志");
    W.$("ws-latex-log-toggle").setAttribute("aria-pressed", showLog);
    viewer?.refresh();
  };
  L.closeSettings = (restore = false) => {
    W.$("ws-latex-settings-popover").hidden = true; W.$("ws-latex-settings").setAttribute("aria-expanded", "false");
    if (restore) W.$("ws-latex-settings").focus();
  };
  L.init = () => {
    const changed = () => { settingsDirty = true; W.run(L.saveSettings)(); };
    W.$("ws-latex-main").onchange = changed; W.$("ws-latex-engine").onchange = changed;
    const font = W.$("ws-latex-font-size"); font.value = L.fontSize();
    font.onchange = () => {
      if (!font.checkValidity()) { font.reportValidity(); return; }
      const fontSize = Math.max(10, Math.min(22, Math.round(Number(font.value) || 12))); font.value = fontSize;
      W.store("latex-ui", { ...PR.ls.get(W.key("latex-ui"), {}), fontSize });
      if (W.view === "latex") W.files.editor?.setFontSize(fontSize);
    };
    W.$("ws-latex-settings").onclick = () => {
      const panel = W.$("ws-latex-settings-popover"), button = W.$("ws-latex-settings");
      if (!panel.hidden) { L.closeSettings(); return; }
      panel.hidden = false; button.setAttribute("aria-expanded", "true");
      const rect = button.getBoundingClientRect();
      panel.style.left = Math.max(8, Math.min(rect.right - panel.offsetWidth, innerWidth - panel.offsetWidth - 8)) + "px";
      panel.style.top = Math.max(8, Math.min(rect.bottom + 6, innerHeight - panel.offsetHeight - 8)) + "px";
    };
    document.addEventListener("pointerdown", event => {
      if (!W.$("ws-latex-settings-popover").contains(event.target) && !W.$("ws-latex-settings").contains(event.target) && !event.target.closest(".pr-dropdown-menu")) L.closeSettings();
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && !event.defaultPrevented && !W.$("ws-latex-settings-popover").hidden) { event.preventDefault(); L.closeSettings(true); }
    });
    window.addEventListener("resize", () => L.closeSettings());
    window.addEventListener("blur", () => L.closeSettings());
    W.$("ws-compile").onclick = W.run(L.compile);
    W.$("ws-compile-cancel").onclick = W.run(async () => {
      W.$("ws-compile-cancel").disabled = true;
      invalidateState();
      try { await W.api("/latex/cancel", {}); invalidateState(); await L.poll(); }
      finally { W.$("ws-compile-cancel").disabled = false; }
    });
    W.$("ws-latex-log-toggle").onclick = () => L.showLog(!showLog);
    W.$("ws-source-locate").onclick = W.run(L.forwardSync);
    W.$("ws-pdf-prev").onclick = () => viewer?.gotoPage(Number(W.$("ws-pdf-page").value) - 1);
    W.$("ws-pdf-next").onclick = () => viewer?.gotoPage(Number(W.$("ws-pdf-page").value) + 1);
    W.$("ws-pdf-page").onchange = event => {
      event.target.value = Math.max(1, Math.min(Number(event.target.max) || 1, Math.round(Number(event.target.value) || 1))); viewer?.gotoPage(event.target.value);
    };
    W.$("ws-pdf-zoom").onchange = event => {
      event.target.value = Math.max(25, Math.min(400, Number(event.target.value) || 100)); viewer?.setZoom(event.target.value);
    };
    W.$("ws-pdf-zoom-out").onclick = () => viewer?.setZoom(Number(W.$("ws-pdf-zoom").value) - 10);
    W.$("ws-pdf-zoom-in").onclick = () => viewer?.setZoom(Number(W.$("ws-pdf-zoom").value) + 10);
    W.$("ws-pdf-fit").onclick = () => viewer?.fitWidth();
    W.$("ws-latex-import").onclick = () => W.$("ws-latex-import-input").click();
    W.$("ws-latex-import-input").onchange = W.run(async event => {
      const file = event.target.files[0]; event.target.value = ""; if (file) await L.importZip(file);
    });
    window.addEventListener("pagehide", event => { if (!event.persisted) { ++pdfSequence; viewer?.destroy(); } });
  };
  L.compile = async () => {
    if (compiling || L.importing || L.data?.compilation?.state === "running") return;
    compiling = true; invalidateState(); L.render();
    try {
      if (!await W.files.flush("latex-workspace/")) throw new Error(PR.t("请先解决文件冲突，再编译。"));
      await L.saveSettings(); compilationRevision = sourceRevision;
      await W.api("/latex/compile", { main: W.$("ws-latex-main").value, engine: W.$("ws-latex-engine").value });
      invalidateState(); L.closeSettings(); await L.poll();
    } finally { compiling = false; L.render(); }
  };
  L.saveSettings = async () => {
    if (settingsPending) return settingsPending;
    const save = async () => {
      do {
        const main = W.$("ws-latex-main").value, engine = W.$("ws-latex-engine").value;
        if (!main || !engine) throw new Error(PR.t("请选择主文件和已安装的编译引擎。"));
        if (!settingsDirty && main === L.data?.main && engine === L.data?.engine) return;
        W.$("ws-latex-settings-status").textContent = PR.t("保存中…");
        invalidateState();
        await W.api("/latex/settings", { main, engine });
        invalidateState();
        if (L.data) Object.assign(L.data, { main, engine });
        settingsDirty = main !== W.$("ws-latex-main").value || engine !== W.$("ws-latex-engine").value;
      } while (settingsDirty);
      W.$("ws-latex-settings-status").textContent = PR.t("已保存");
    };
    settingsPending = save();
    try { await settingsPending; }
    catch (error) { settingsDirty = true; W.$("ws-latex-settings-status").textContent = error.message; throw error; }
    finally { settingsPending = null; }
  };
  L.enter = async () => {
    invalidateState();
    const seq = stateSequence, key = W.key("latex-read"), data = await W.api("/latex");
    if (seq !== stateSequence || key !== W.key("latex-read")) return;
    L.data = data; stateKey = key; stateVersion = data.version || "";
    settingsDirty = false; signature = ""; L.render();
  };
  L.poll = async () => {
    if (!L.data || L.importing || settingsPending) return;
    const seq = ++stateSequence, key = W.key("latex-read");
    const query = key === stateKey && stateVersion ? "?" + new URLSearchParams({ if_version: stateVersion }) : "";
    const data = await W.api("/latex" + query);
    if (seq !== stateSequence || key !== W.key("latex-read") || L.importing) return;
    if (!data.unchanged) { L.data = data; stateKey = key; stateVersion = data.version || ""; }
    L.render();
  };
  L.updateFiles = () => {
    if (!L.data || W.view !== "latex") return;
    const select = W.$("ws-latex-main"), selected = settingsDirty || settingsPending ? select.value : L.data.main;
    const paths = W.files.entries.filter(entry => entry.kind === "file" && entry.path.startsWith("latex-workspace/") && /\.tex$/i.test(entry.path)).map(entry => entry.path.replace(/^latex-workspace\//, ""));
    if (selected && !paths.includes(selected)) paths.push(selected);
    const next = JSON.stringify(paths);
    if (select.dataset.paths !== next) { select.replaceChildren(...paths.map(path => PR.el("option", { value: path, text: path }))); select.dataset.paths = next; }
    if (selected) select.value = selected;
  };
  L.diagnostics = () => {
    if (!L.data || !W.files.active || !W.files.editor) return;
    W.files.editor.diagnostics((L.data.compilation?.errors || []).filter(error => sourcePath(error.file || L.data.main) === W.files.active.path));
  };
  async function getViewer() {
    if (!viewerPromise) viewerPromise = import("/web/vendor/workspace-pdf.js").then(module => {
      viewer = module.create(W.$("ws-pdf"), {
        onChange: state => {
          if (document.activeElement !== W.$("ws-pdf-page")) W.$("ws-pdf-page").value = state.page;
          if (document.activeElement !== W.$("ws-pdf-zoom")) W.$("ws-pdf-zoom").value = state.zoom;
          W.$("ws-pdf-page").max = Math.max(1, state.pages); W.$("ws-pdf-pages").textContent = "/ " + state.pages;
          W.$("ws-pdf-prev").disabled = state.page <= 1; W.$("ws-pdf-next").disabled = state.page >= state.pages;
          W.$("ws-pdf-fit").setAttribute("aria-pressed", state.fit);
        },
        onInverseSync: W.run(L.inverseSync), onError: error => L.syncStatus(PR.t("PDF 载入失败：{error}", { error: error.message }))
      }); return viewer;
    }).catch(error => { viewerPromise = null; throw error; });
    return viewerPromise;
  }
  async function showPdf(pdf, version) {
    const seq = ++pdfSequence;
    if (!pdf) { pdfPath = ""; pdfVersion = ""; pdfLoading = false; viewer?.clear(); L.syncStatus(""); L.showLog(showLog); return; }
    pdfPath = pdf; pdfLoading = true; L.showLog(showLog);
    const url = W.raw(W.safePath(sourcePath(pdf), "latex-workspace"), version);
    W.$("ws-pdf-download").href = url;
    W.$("ws-pdf-download").download = (L.data?.main || "main.tex").split("/").pop().replace(/\.tex$/i, ".pdf");
    W.$("ws-pdf-empty").textContent = PR.t("正在载入 PDF…");
    try {
      const instance = await getViewer(); if (seq !== pdfSequence) return;
      await instance.load(url); if (seq !== pdfSequence) return;
      pdfLoading = false; L.showLog(showLog); L.syncStatus(sourceStale ? PR.t("源码已更改；请重新编译后定位。") : "");
    } catch (error) {
      if (seq !== pdfSequence) return;
      pdfPath = ""; pdfVersion = ""; pdfLoading = false; L.showLog(showLog);
      W.$("ws-pdf-empty").textContent = PR.t("PDF 载入失败：{error}", { error: error.message });
    }
  }
  L.render = () => {
    if (!L.data) return;
    const data = L.data, compilation = data.compilation || { state: "idle" };
    if (!settingsDirty && !settingsPending) {
      L.updateFiles();
      const select = W.$("ws-latex-engine"), engines = JSON.stringify(data.engines || []);
      if (select.dataset.engines !== engines) {
        select.replaceChildren(); select.dataset.engines = engines;
        (data.engines || []).forEach(value => {
          const item = typeof value === "string" ? { id: value, installed: true } : value, unavailable = item.installed === false || item.available === false;
          select.append(PR.el("option", { value: item.id || item.name, disabled: unavailable, text: (item.id || item.name) + (unavailable ? " · " + PR.t("未安装") : "") }));
        });
      }
      if (data.engine) select.value = data.engine;
    }
    const running = compilation.state === "running";
    W.$("ws-compile").disabled = compiling || L.importing || running || !W.$("ws-latex-engine").selectedOptions[0] || W.$("ws-latex-engine").selectedOptions[0].disabled;
    W.$("ws-compile-cancel").hidden = !running; W.$("ws-compile").hidden = running;
    W.$("ws-source-locate").disabled = running || compilation.state !== "done" || !compilation.synctex;
    W.$("ws-latex-import").disabled = running || compiling || L.importing;
    W.$("ws-compile-status").textContent = ({ idle: PR.t("尚未编译"), running: PR.t("编译中…"), done: PR.t("编译成功"), error: PR.t("编译失败"), interrupted: PR.t("已中断") })[compilation.state] || compilation.state;
    const next = JSON.stringify(compilation);
    if (next === signature) return;
    signature = next; ++syncSequence;
    if (running && compilationRevision === null) compilationRevision = sourceRevision;
    W.$("ws-latex-log-text").textContent = compilation.log || PR.t("尚无编译日志");
    const errors = W.$("ws-latex-errors"); errors.replaceChildren();
    (compilation.errors || []).forEach(error => errors.append(W.button((error.file || data.main) + ":" + (error.line || 1) + " — " + error.message,
      () => W.files.open(W.safePath(sourcePath(error.file || data.main), "latex-workspace"), error.line), "log")));
    if (compilation.state === "error") showLog = true;
    else if (compilation.state === "done") showLog = false;
    const pdf = compilation.state === "done" && compilation.pdf;
    W.$("ws-pdf-download").hidden = !pdf;
    W.$("ws-pdf-empty").textContent = PR.t("保存并编译后，在此预览 PDF。编译失败时不会展示过期结果。");
    if (pdf) {
      const version = String(compilation.finished || compilation.updated || pdf);
      if (version !== pdfVersion || pdfPath !== pdf) {
        sourceStale = compilationRevision !== null && compilationRevision !== sourceRevision;
        compilationRevision = null; pdfVersion = version; void showPdf(pdf, version);
      }
    } else if (!running) { compilationRevision = null; void showPdf("", ""); }
    // Retain the previous preview (with the visible compiling status) until the
    // replacement is ready, so long documents do not jump back to page one.
    L.showLog(showLog); L.diagnostics();
  };
  function checkSync() {
    if (!pdfPath || pdfLoading || L.data?.compilation?.state !== "done") throw new Error(PR.t("请先成功编译，再使用源码定位。"));
    const saved = PR.ls.get(W.key("drafts"), {});
    if (sourceStale || Object.keys(saved).some(path => path.startsWith("latex-workspace/")) || [...W.files.buffers.values()].some(buffer => buffer.path.startsWith("latex-workspace/") && (buffer.dirty || buffer.conflict))) throw new Error(PR.t("源码已更改；请重新编译后定位。"));
    return pdfPath;
  }
  L.forwardSync = async () => {
    const seq = ++syncSequence;
    try {
      const pdf = checkSync(), buffer = W.files.active, position = W.files.editor?.position();
      if (!buffer || !position || !buffer.path.startsWith("latex-workspace/")) throw new Error(PR.t("请先打开 LaTeX 源文件。"));
      const result = await W.api("/latex/forward-sync", { pdf, path: buffer.path, ...position });
      if (seq !== syncSequence || pdf !== pdfPath) return;
      if (!result.found || !result.matches?.length) { L.syncStatus(PR.t("此位置没有 SyncTeX 映射。")); return; }
      W.showPdf(); L.showLog(false);
      if (!await (await getViewer()).highlight(result.matches)) { L.syncStatus(PR.t("此位置没有 SyncTeX 映射。")); return; }
      L.syncStatus(PR.t("已定位到编译结果；双击 PDF 可返回源码。"));
    } catch (error) { if (seq === syncSequence) L.syncStatus(error.status === 409 ? PR.t("编译结果已过期；请重新编译后定位。") : error.message); }
  };
  L.inverseSync = async position => {
    const seq = ++syncSequence;
    try {
      const pdf = checkSync();
      const result = await W.api("/latex/inverse-sync", { pdf, ...position });
      if (seq !== syncSequence || pdf !== pdfPath) return;
      if (!result.found) { L.syncStatus(PR.t("此位置没有 SyncTeX 映射。")); return; }
      const path = W.safePath(result.path, "latex-workspace");
      if (!await W.files.open(path, result.line)) return;
      W.files.editor?.gotoLine(result.line, result.column); L.syncStatus(PR.t("已定位到源码：{path}:{line}", { path, line: result.line }));
    } catch (error) { if (seq === syncSequence) L.syncStatus(error.status === 409 ? PR.t("编译结果已过期；请重新编译后定位。") : error.message); }
  };
  L.importZip = async file => {
    if (L.importing || compiling) return;
    if (!/\.zip$/i.test(file.name)) throw new Error(PR.t("请选择 LaTeX ZIP 文件。"));
    if (file.size > 128 * 1024 * 1024) throw new Error(PR.t("ZIP 文件不能超过 128 MiB。"));
    if (L.data?.compilation?.state === "running" || W.sessions.items.some(session => session.state === "running" || session.terminal_open)) throw new Error(PR.t("请先停止项目中的 AI 和编译任务，再导入 ZIP。"));
    if (!await PR.confirm({ title: PR.t("导入 LaTeX ZIP？"), body: file.name + "\n" + PR.t("解压到 latex-workspace，自动识别主文件。不会自动编译；同名文件将先请求覆盖确认。"), ok: PR.t("导入") })) return;
    invalidateState();
    L.importing = W.files.importing = true; W.files.editor?.setReadOnly(true);
    W.$("ws-sidebar").inert = true; W.$("ws-file-pane").inert = true; W.$("ws-chat").inert = true; document.querySelector(".topbar").inert = true; L.render();
    let data;
    try {
      if (!await W.files.flush("latex-workspace/")) throw new Error(PR.t("请先解决文件冲突，再导入 ZIP。"));
      if (settingsDirty || settingsPending) await L.saveSettings();
      try { data = await W.api("/latex/import-zip?replace=0", file, true); }
      catch (error) {
        if (error.status !== 409 || error.code !== "file_conflict") throw error;
        if (!await PR.confirm({ title: PR.t("覆盖 ZIP 中的同名文件？"), body: error.message + "\n\n" + PR.t("只替换这些同名文件，保留其他项目文件。此操作不可撤销。"), danger: true, ok: PR.t("覆盖并导入") })) return;
        data = await W.api("/latex/import-zip?replace=1", file, true);
      }
      L.data = data; settingsDirty = false; signature = "";
      // Buffers were flushed and editing locked. Forget only imported clean snapshots.
      for (const path of data.imported || []) W.files.buffers.delete(path);
      PR.toast(PR.t("已导入 {n} 个文件", { n: (data.imported || []).length }));
    } finally {
      L.importing = W.files.importing = false; W.files.editor?.setReadOnly(false);
      W.$("ws-sidebar").inert = false; W.$("ws-file-pane").inert = false; W.$("ws-chat").inert = false; document.querySelector(".topbar").inert = false; L.render();
      W.files.buffers.forEach(buffer => W.files.scheduleSave(buffer));
    }
    if (data) {
      await W.files.refresh();
      if (data.main) await W.files.open(W.safePath(sourcePath(data.main), "latex-workspace"));
      L.render();
    }
  };
  L.leave = async () => {
    if (L.importing) return false;
    invalidateState(); L.closeSettings(); ++syncSequence;
    if (!settingsDirty && !settingsPending) return true;
    await L.saveSettings(); return true;
  };
})(window.PR, window.PR.workspaces);

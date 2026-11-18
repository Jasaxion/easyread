/* URL import submissions and the import adapter for the shared task monitor. */
(function (PR) {
  "use strict";
  if (document.getElementById?.("pr-data") || !["http:", "https:"].includes(location.protocol)) return;
  const API = "/api/import-tasks";
  const terminal = new Set(["completed", "failed", "cancelled", "interrupted"]);
  const pending = new Map(), tasks = new Map(), operations = new Map(), refreshed = new Set();
  let scope = "", generation = 0, libraryRefreshScheduled = false, reconciled = false, reconciling = false;
  const esc = (value) => PR.esc(String(value ?? ""));
  const storageKey = () => "paperread:import-submissions:" + (scope || "unbound");
  const active = (task) => ["submitting", "queued", "running", "cancelling"].includes(task.state);
  const refresh = (oldId, newId) => PR.researchTaskMonitor?.refresh(oldId, newId);
  const endpoint = (task, action) => API + "/" + encodeURIComponent(task.id) + "/" + action;
  const documentId = (task) => task.result?.id || task.paper_id;

  function persist() {
    try {
      sessionStorage.setItem(storageKey(), JSON.stringify([...pending.values()].map(({ request, error_reason }) => ({ request, error_reason }))));
    } catch (_) { /* Accepted tasks still persist on the server; pending retries survive in this page. */ }
  }
  function localTask(request, state = "unconfirmed", error_reason = "") {
    return { id: "import-submit-" + request.request_id, kind: "import", ref: request.ref, title: request.ref,
      request_id: request.request_id, request, scope: request.scope, state, error_reason };
  }
  function restore(key = storageKey()) {
    try {
      const saved = JSON.parse(sessionStorage.getItem(key) || "[]");
      if (!Array.isArray(saved)) return;
      saved.forEach(({ request, error_reason }) => {
        if (!request?.ref || !/^[a-f0-9]{32}$/.test(request.request_id)) return;
        const task = localTask(request, "unconfirmed", error_reason);
        if (!pending.has(task.id)) pending.set(task.id, task);
      });
    } catch (_) { /* Storage is optional and scoped only after an authenticated monitor response. */ }
  }
  function refreshLibrary(task) {
    if (!terminal.has(task.state)) return;
    const signature = task.id + ":" + task.state + ":" + (documentId(task) || "");
    if (refreshed.has(signature) || !PR.lib?.load) return;
    refreshed.add(signature);
    if (libraryRefreshScheduled) return;
    libraryRefreshScheduled = true;
    Promise.resolve().then(() => {
      libraryRefreshScheduled = false;
      return PR.lib.load();
    }).catch(() => { refreshed.clear(); });
  }
  function accept(raw, localId) {
    if (!raw?.id || raw.kind !== "import") throw new Error(PR.t("未收到导入任务确认"));
    const previous = tasks.get(raw.id);
    // A late POST response must not replace progress already observed by the monitor.
    const task = previous && (terminal.has(previous.state) || (raw.state === "queued" && previous.state !== "queued")) ? previous : raw;
    tasks.set(task.id, task);
    for (const [id, submission] of pending) {
      if (id === localId || submission.request_id === task.request_id) {
        pending.delete(id);
        refresh(id, task.id);
      }
    }
    persist();
    refreshLibrary(task);
    refresh();
    return task;
  }
  function sync(rawTasks, scopeId) {
    if (scopeId && scopeId !== scope) {
      if (scope) { generation++; pending.clear(); tasks.clear(); operations.clear(); refreshed.clear(); reconciled = false; reconciling = false; }
      const firstScope = !scope;
      scope = scopeId;
      restore();
      if (firstScope) {
        restore("paperread:import-submissions:unbound");
        try { sessionStorage.removeItem("paperread:import-submissions:unbound"); } catch (_) {}
      }
    }
    const imports = rawTasks.filter((task) => task.kind === "import");
    for (const raw of imports) {
      const previous = tasks.get(raw.id);
      const current = previous && terminal.has(previous.state) && active(raw) ? previous
        : {...previous, ...raw, acknowledged:!!(previous?.acknowledged || raw.acknowledged)};
      tasks.set(raw.id, current);
      for (const [id, submission] of pending) {
        if (submission.request_id === raw.request_id) { pending.delete(id); refresh(id, raw.id); }
      }
      refreshLibrary(current);
      const op = operations.get(raw.id);
      if (op?.action === "cancel" && (raw.state === "cancelling" || terminal.has(raw.state))) operations.delete(raw.id);
      if (op?.action === "ack" && raw.acknowledged) operations.delete(raw.id);
    }
    for (const task of imports) {
      if (task.retry_of && operations.get(task.retry_of)?.action === "retry") retryAccepted(task.retry_of, task);
    }
    persist();
  }
  async function reconcile(scopeId) {
    if (!scopeId || scopeId !== scope || reconciling) return;
    const known = PR.researchTaskMonitor?.visibleTaskIds?.() || new Set();
    const missing = [...tasks.values()].filter(task => active(task) && !known.has(task.id));
    if (reconciled && !missing.length && !pending.size) return;
    const currentGeneration = generation;
    reconciling = true;
    try {
      const result = await PR.api(API);
      if (generation !== currentGeneration || result.scope_id !== scope) return;
      sync(result.tasks || [], scope); reconciled = true;
      const found = new Set((result.tasks || []).map(task => task.id));
      for (const task of missing) if (!found.has(task.id)) {
        const detail = await PR.api(API + "/" + encodeURIComponent(task.id));
        if (generation !== currentGeneration) return;
        if (detail.task) accept(detail.task);
      }
      refresh();
    } catch (_) { /* A failed status read is not an import failure. */ }
    finally { if (generation === currentGeneration) reconciling = false; }
  }
  function title(task) {
    if (task.state === "submitting") return PR.t("正在提交导入…");
    if (task.state === "unconfirmed") return PR.t("导入提交状态待确认");
    if (task.state === "completed") return task.result_state === "partial" ? PR.t("文献已导入，后续处理未完成") : PR.t("导入已完成");
    if (task.state === "cancelled") return PR.t("导入已取消");
    if (task.state === "interrupted") return PR.t("导入已中断");
    if (task.state === "failed") return PR.t("导入未完成");
    if (task.state === "cancelling") return PR.t("正在取消导入…");
    return PR.t("正在导入…");
  }
  function presentation(task) {
    const request = task.request || {}, op = operations.get(task.id);
    const local = pending.has(task.id), working = active(task), hasDocument = !!documentId(task);
    const row = (name, value) => value ? '<div class="pr-task-row"><span class="pr-task-label">' + esc(name) + '</span><span class="pr-task-val">' + esc(value) + '</span></div>' : "";
    const button = (action, text, style = "line") => '<button class="btn ' + style + '" type="button" data-task-' + action + (op?.busy ? ' disabled' : '') + '>' + esc(text) + '</button>';
    let availability = local ? PR.t("待确认") : PR.t("文献尚未保存");
    if (hasDocument) availability = task.state === "completed" && task.result_state !== "partial"
      ? PR.t("文献已入库；后续翻译或 PDF 生成请查看文献状态。")
      : PR.t("文献已保存，但本次处理未完成；可打开已有文献。");
    const retryPdf = task.state === "completed" && task.result_state === "partial" && request.pdf_export;
    const help = retryPdf ? PR.t("可单独重试 PDF 生成，不会重复导入或翻译正文。") : local ? PR.t("尚未确认服务器是否接收；确认或重试会使用同一请求，不会重复导入。")
      : working ? PR.t("可继续阅读或在背景等待，完成后将自动通知。")
      : task.state === "completed" ? PR.t("可打开文献继续阅读。") : PR.t("可手动重试；不会自动重新导入。");
    let controls = hasDocument ? button("view", PR.t("打开文献"), "accent") : "";
    if (working && !local && task.state !== "cancelling" && task.phase !== "handoff") controls += button("cancel", PR.t("取消导入"), "danger");
    if (task.state === "unconfirmed") controls += button("retry", PR.t("确认或重试"), "accent") + button("ack", PR.t("不再提醒（不取消后台任务）"));
    else if (retryPdf) controls += button("retry", PR.t("重试 PDF 生成"), "accent");
    else if (terminal.has(task.state) && task.state !== "completed") controls += button("retry", PR.t("重试"), "accent");
    if (terminal.has(task.state) && !task.acknowledged) controls += button("ack", PR.t("知道了"));
    controls += '<button class="btn line" type="button" data-task-continue>' + PR.t("关闭") + '</button>';
    const mode = request.read ? PR.t("读英文原文") : request.translate ? PR.t("翻译成") + " " + (PR.aiOutputLanguage?.(request.target) || request.target || "") : PR.t("先不处理");
    const progress = task.total > 0 ? PR.t("{done} / {total}", { done: task.done || 0, total: task.total }) : "";
    const range = /^range:(\d+)-(\d+)$/.exec(request.scope || task.scope || "");
    const importScope = range ? PR.t("第 {from} 到 {to} 页", { from: range[1], to: range[2] })
      : ({ all: PR.t("全文"), body: PR.t("正文（到参考文献为止）") }[request.scope || task.scope] || request.scope || task.scope);
    const kind = { auto: PR.t("自动识别"), pdf: PR.t("PDF 论文"), web: PR.t("网页 / 博客（保留原排版）") }[request.kind] || request.kind;
    return { title: title(task), controls, detailsMarkup: '<div class="pr-task-details">' +
      row(PR.t("导入来源"), task.ref || task.title || request.ref) +
      row(PR.t("正在做什么"), PR.t(task.stage_label || task.message || title(task))) +
      row(PR.t("进度"), progress) +
      row(PR.t("导入后"), task.request ? mode : "") +
      row(PR.t("范围"), importScope) +
      row(PR.t("链接类型"), kind) +
      row(PR.t("模型"), request.model) +
      row(PR.t("额外要求"), request.translation_extra) +
      row(PR.t("生成中文 PDF"), request.pdf_export ? PR.t("是") : "") +
      row(PR.t("输出目录"), request.pdf_export ? request.pdf_output_dir : "") +
      row(PR.t("结果可用性"), availability) +
      row(local ? PR.t("请求状态") : PR.t("失败原因："), task.error_reason ? PR.t(task.error_reason) : "") +
      row(PR.t("请求状态"), op?.busy ? PR.t("处理中…") : op?.error) +
      row(PR.t("下一步"), help) + '</div>' };
  }
  async function send(task) {
    if (task.state === "submitting") return;
    task.state = "submitting";
    task.error_reason = "";
    const attempt = task.attempt = (task.attempt || 0) + 1, submittedGeneration = generation;
    persist(); refresh();
    const uncertain = (message = "") => {
      if (generation !== submittedGeneration || !pending.has(task.id) || task.attempt !== attempt) return;
      task.state = "unconfirmed"; task.error_reason = message;
      persist(); refresh();
    };
    // Do not abort a write or treat a deadline as failure: its response may arrive later.
    const timer = setTimeout(() => uncertain(), 15000);
    try {
      const target = await PR.api(API);
      if ((task.request.library_scope && task.request.library_scope !== target.scope_id) ||
          (task.request.library_id && task.request.library_id !== target.library_id)) throw new Error(PR.t("文献库已切换，请在当前文献库重新发起导入"));
      if (generation !== submittedGeneration) return;
      task.request.library_scope = target.scope_id;
      task.request.library_id = target.library_id;
      if (!scope && target.scope_id) {
        scope = target.scope_id; restore(); restore("paperread:import-submissions:unbound");
        try { sessionStorage.removeItem("paperread:import-submissions:unbound"); } catch (_) {}
      }
      persist();
      const found = (target.tasks || []).find(item => item.request_id === task.request_id);
      const result = found ? {task:found} : await PR.api(API, { method: "POST", body: { ...task.request } });
      if (generation === submittedGeneration) accept(result.task, task.id);
    } catch (error) { uncertain(error.message); }
    finally { clearTimeout(timer); }
  }
  function submit(body) {
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    const request_id = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    const task = localTask({ ...body, request_id, ...(scope ? {library_scope:scope} : {}), ...(PR.pwa?.scope ? {library_id:PR.pwa.scope} : {}) });
    pending.set(task.id, task);
    return send(task);
  }
  function retryAccepted(sourceId, task) {
    operations.delete(sourceId);
    accept(task);
    refresh(sourceId, task.id);
    const source = tasks.get(sourceId);
    if (source) source.retry_task_id = task.id;
    if (source && !source.acknowledged) action(source, "ack");
  }
  async function action(task, name) {
    if (name === "ack" && pending.has(task.id)) { pending.delete(task.id); persist(); refresh(); return; }
    if (name === "retry" && pending.has(task.id)) return send(pending.get(task.id));
    if (operations.get(task.id)?.busy) return;
    const actionGeneration = generation;
    const op = { action: name, busy: true, error: "" };
    operations.set(task.id, op); refresh();
    const uncertain = (message = "") => {
      if (operations.get(task.id) !== op) return;
      op.busy = false;
      op.error = PR.t("请求状态待确认，请查看任务进度或重试。") + (message ? " " + message : "");
      refresh();
    };
    const timer = setTimeout(() => uncertain(), 15000);
    try {
      const childId = name === "retry" && task.retry_task_id;
      const result = childId
        ? await PR.api(API + "/" + encodeURIComponent(childId))
        : await PR.api(endpoint(task, name), { method: "POST", body: {} });
      if (generation !== actionGeneration) return;
      if (!result.task?.id || result.task.kind !== "import") throw new Error(PR.t("未收到导入任务确认"));
      if (name === "retry") retryAccepted(task.id, result.task);
      else {
        // An ack response can update an already terminal task without downgrading its result.
        if (name === "ack" && tasks.has(task.id)) tasks.get(task.id).acknowledged = result.task.acknowledged;
        accept(result.task);
        operations.delete(task.id); refresh();
      }
    } catch (error) { uncertain(error.message); }
    finally { clearTimeout(timer); }
  }
  PR.importTasks = {
    submit, sync, reconcile, presentation, active,
    terminal: (task) => terminal.has(task.state) || task.state === "unconfirmed",
    label: (task) => PR.t("链接导入") + " · " + (task.ref || task.title || ""),
    entryText: (task) => title(task) + " · " + (task.ref || task.title || ""),
    badge: (task) => task.state === "submitting" ? PR.t("提交中") : task.state === "unconfirmed" ? PR.t("待确认") : "",
    list: () => [...tasks.values(), ...pending.values()],
    cancel: (task) => action(task, "cancel"),
    retry: (task) => action(task, "retry"),
    ack: (task) => terminal.has(task.state) || task.state === "unconfirmed" ? action(task, "ack") : undefined,
    open(task) { if (documentId(task)) PR.navigateWorkspace("/read/" + encodeURIComponent(documentId(task))); }
  };
})(window.PR);

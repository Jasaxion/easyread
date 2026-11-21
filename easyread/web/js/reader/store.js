/* 阅读页的数据与保存。
   - paper / discussion / layout / job 来自翻译方，只读；
   - reader（我的修改、笔记、手写、论文笔记、进度）只通过“操作”改：先进浏览器里的待存队列，再发给本地服务，
     服务确认写进 reader.json 后才从队列删。断网、服务没开、页面崩了，队列都还在，下次自动补发。
   - 离线版（导出的单文件）没有服务，队列本身就是存储，可导出后并回。 */
(function (PR) {
  "use strict";
  const S = (PR.state = { paper: null, discussion: { entries: [] }, reader: {}, layout: {}, item: {}, job: {}, images: {}, versions: {}, engine: "none" });
  let mode = "server";
  let serverReader = null, readerBase = null, ipadPolling = false;
  let outbox = [], persistedOutbox = [];
  let outboxKey = "";
  let storageFailed = false;
  let flushing = false, retryMs = 1500, retryT = null, flushT = null;
  let flushPromise = null, flushOptions = null;
  const ipad = () => PR.ipadRuntime?.enabled() === true;
  const inputBusy = () => PR.ipadRuntime?.busy() === true;
  const client = PR.uid("c");
  PR.pid = decodeURIComponent((location.pathname.match(/\/read\/([^/]+)/) || [])[1] || "");
  const base = () => "/api/p/" + PR.pid;

  PR.store = { get mode() { return mode; }, get pending() { return outbox.length; }, inputBusy };

  /* ---------- 和 Python store.apply_ops 同一套规则 ---------- */
  function applyOps(reader, ops) {
    reader.edits = reader.edits || {};
    reader.notes = reader.notes || {};
    reader.ai_annotations = reader.ai_annotations || {};
    reader.ink = reader.ink || {};
    reader.stickers = reader.stickers || {};
    reader.progress = reader.progress || {};
    for (const op of ops) {
      const at = op.at || PR.nowIso();
      if (op.op === "edit") {
        const cur = reader.edits[op.block];
        if (cur && (cur.at || "") > at) continue;
        if (op.zh == null) { if (cur) reader.edits[op.block] = { reverted: true, prev: cur.zh, at }; }
        else {
          const e = { zh: op.zh, base: op.base || "", at };
          if (cur && cur.zh && cur.zh !== op.zh) e.prev = cur.zh;
          reader.edits[op.block] = e;
        }
      } else if (op.op === "note") {
        const cur = reader.notes[op.note.id];
        if (cur && (cur.updated || "") > (op.note.updated || "")) continue;
        reader.notes[op.note.id] = JSON.parse(JSON.stringify(op.note));
      } else if (op.op === "note_del") {
        const cur = reader.notes[op.id];
        if (!cur || at >= (cur.updated || "")) reader.notes[op.id] = { ...(cur || { id: op.id }), deleted: true, updated: at };
      } else if (op.op === "ai_annotation") {
        const annotation = op.annotation || {};
        const id = annotation.annotation_id || annotation.id;
        const cur = id && reader.ai_annotations[id];
        if (id && (!cur || (annotation.updated_at || at) >= (cur.updated_at || ""))) reader.ai_annotations[id] = { ...annotation, annotation_id: id };
      } else if (op.op === "ink" || op.op === "ink_del") {
        const stroke = op.op === "ink" ? op.stroke : { id: op.id };
        const id = stroke && String(stroke.id || "");
        const cur = id && reader.ink[id];
        if (id && (!cur || at >= (cur.updated || ""))) reader.ink[id] = op.op === "ink"
          ? { ...stroke, updated: at }
          : { ...(cur || { id }), deleted: true, updated: at };
      } else if (op.op === "sticker" || op.op === "sticker_del") {
        const sticker = op.op === "sticker" ? op.sticker : { id: op.id };
        const id = sticker && String(sticker.id || "");
        const cur = id && reader.stickers[id];
        if (id && (!cur || at >= (cur.updated || ""))) reader.stickers[id] = op.op === "sticker"
          ? { ...sticker, updated: at }
          : { ...(cur || { id }), deleted: true, updated: at };
      } else if (op.op === "paper_note") {
        const cur = reader.paper_note || {};
        if (at >= (cur.at || "")) reader.paper_note = { body: op.body || "", at };
      } else if (op.op === "progress") {
        if (at >= (reader.progress.at || "")) {
          Object.assign(reader.progress, { block: op.block, at });
          delete reader.progress.off;
          for (const key of ["ratio", "off"]) if (op[key] != null) reader.progress[key] = op[key];
        }
      }
    }
    return reader;
  }

  function rebuildReader() { S.reader = applyOps(JSON.parse(JSON.stringify(serverReader || {})), outbox); readerBase = serverReader; }
  function storageWarning() { setStatus("error", PR.t("浏览器存储已满，修改只在内存里，请尽快导出")); }
  function saveOutbox() {
    const delta = ipad() && PR.pwa?.scope && PR.pwa.persistOutboxDelta;
    storageFailed = !(delta ? PR.pwa.persistOutboxDelta(PR.pid, outbox, persistedOutbox) : PR.pwa?.scope ? PR.pwa.persistOutbox(PR.pid, outbox) : PR.ls.set(outboxKey, outbox)) && outbox.length > 0;
    if (storageFailed) storageWarning();
    else if (PR.pwa?.scope) {
      if (!delta) PR.pwa.ack(PR.pid, persistedOutbox.filter(op => !outbox.includes(op)));
      persistedOutbox = outbox.slice();
    }
  }
  function setStatus(s, text) { PR.store.status = s; PR.emit("status", { s, text, pending: outbox.length }); }
  function statusIdle() {
    if (storageFailed) { storageWarning(); return; }
    if (mode === "static") setStatus("local", outbox.length ? PR.t("存在本浏览器") : PR.t("离线版"));
    else if (PR.pwa?.offline) setStatus("offline", PR.t("离线阅读；修改将暂存在本设备"));
    else setStatus("saved", PR.t("已保存"));
  }

  /* 页面调用这个提交修改 */
  PR.commit = function (op) {
    op.at = op.at || PR.nowIso();
    const noteId = op.op === "note" ? op.note.id : op.op === "note_del" ? op.id : null;
    const previous = noteId && S.reader.notes?.[noteId];
    // 同一目标还没发出去的旧操作合并掉，避免队列无限长
    if (op.op === "note") outbox = outbox.filter((o) => !(o.op === "note" && o.note.id === op.note.id));
    if (op.op === "ink" || op.op === "ink_del") {
      const id = op.op === "ink" ? op.stroke.id : op.id;
      const current = S.reader.ink && S.reader.ink[id];
      if (current && (current.updated || "") > op.at) return;
      outbox = outbox.filter(o => !(o.op === op.op && (op.op === "ink" ? o.stroke.id : o.id) === id));
    }
    if (op.op === "sticker" || op.op === "sticker_del") {
      const id = op.op === "sticker" ? op.sticker.id : op.id;
      const current = S.reader.stickers && S.reader.stickers[id];
      if (current && (current.updated || "") > op.at) return;
      outbox = outbox.filter(o => !(o.op === op.op && (op.op === "sticker" ? o.sticker.id : o.id) === id));
    }
    if (op.op === "edit") outbox = outbox.filter((o) => !(o.op === "edit" && o.block === op.block));
    if (op.op === "progress" || op.op === "paper_note") outbox = outbox.filter((o) => o.op !== op.op);
    outbox.push(op);
    saveOutbox();
    if (ipad() && readerBase === serverReader && ["ink", "ink_del"].includes(op.op)) applyOps(S.reader, [op]);
    else if (ipad() && readerBase === serverReader && op.op === "progress")
      S.reader.progress = applyOps({progress: JSON.parse(JSON.stringify(serverReader?.progress || {}))}, [op]).progress;
    else rebuildReader();
    if (ipad() && ["ink", "ink_del"].includes(op.op)) PR.ipadRuntime.mark();
    const changed = noteId ? [previous, S.reader.notes?.[noteId]].filter(Boolean) : [];
    if (changed.length && changed.every(n => n.kind === "highlight" && n.anchor && !n.parts?.length)) {
      PR.emit("reader", { ...op, highlightBlocks: [...new Set(changed.map(n => n.anchor))] });
    } else PR.emit("reader", op);
    if (mode === "server") { if (op.op !== "progress" && !storageFailed) setStatus("saving", PR.t("保存中")); scheduleFlush(op.op === "progress" ? 3000 : 250); }
    else statusIdle();
  };

  function scheduleFlush(ms) { clearTimeout(flushT); flushT = setTimeout(() => { if (inputBusy()) scheduleFlush(120); else flush(); }, ms); }

  function flush(opts) {
    if (flushPromise) { if (opts?.immediate) flushOptions.immediate = true; return flushPromise; }
    if (mode !== "server" || !outbox.length) return Promise.resolve(true);
    flushOptions = {...opts};
    flushPromise = sendBatch(flushOptions).finally(() => { flushPromise = null; flushOptions = null; });
    return flushPromise;
  }

  async function sendBatch(opts) {
    flushing = true;
    const batch = outbox.slice();
    try {
      if (ipad()) await PR.ipadRuntime.wait(opts);
      if (PR.pwa?.blocked) throw new Error(PR.t("请重新授权后同步修改"));
      if (PR.pwa && !PR.token) await reloadToken();
      const r = await fetch(base() + "/ops", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Token": PR.token || "" },
        body: JSON.stringify({ ops: batch, client }),
        keepalive: !!(opts && opts.keepalive) || batch.every(o => o.op === "progress"),
      });
      if (r.status === 403) { await reloadToken(); throw new Error("token"); }
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
      const res = await r.json();
      if (ipad()) await PR.ipadRuntime.wait(opts);
      if (!(ipad() && PR.pwa?.scope)) {
        serverReader = applyOps(JSON.parse(JSON.stringify(serverReader || {})), batch); serverReader.rev = res.rev;
      }
      // 只认 reader 的新版本：paper / layout 等要由轮询去取内容，这里提前记上版本号，轮询就以为没变、不再重画
      if (res.versions && res.versions.reader) S.versions.reader = res.versions.reader;
      // Fetch the complete acknowledged state, including writes from other tabs.
      // Persist it BEFORE dropping the durable outbox; retries remain idempotent.
      if (PR.pwa?.scope) {
        const confirmed = await PR.pwa.json(base() + "/part/reader");
        if (ipad()) await PR.ipadRuntime.wait(opts);
        else { serverReader = confirmed.data; S.versions.reader = confirmed.version; }
        await PR.pwa.save(PR.pid, {...S, reader: confirmed.data});
        if (ipad()) await PR.ipadRuntime.wait(opts);
        serverReader = confirmed.data; S.versions.reader = confirmed.version;
      }
      PR.pwa?.ack(PR.pid, batch); // Includes partially persisted keys not yet in persistedOutbox.
      const sent = ipad() ? new Set(batch) : null;
      outbox = outbox.filter(o => sent ? !sent.has(o) : !batch.includes(o));
      saveOutbox();
      rebuildReader();
      // A saved translation edit can rebase AI anchors in the acknowledged
      // snapshot. Its version is already current, so polling alone won't repaint.
      if (batch.some(o => o.op === "edit" || (o.op === "note" && o.note?.ai_annotation_id)) && PR.pwa?.scope)
        PR.emit("reader", { op: "source-anchor-refresh" });
      retryMs = 1500;
      if (outbox.length) scheduleFlush(50); else statusIdle();
      return true;
    } catch (e) {
      if (storageFailed) storageWarning();
      else setStatus("offline", PR.t("未连上本地服务，{n} 条修改暂存在浏览器", { n: outbox.length }));
      clearTimeout(retryT);
      retryT = setTimeout(flush, retryMs);
      retryMs = Math.min(retryMs * 2, 15000);
      return false;
    } finally {
      flushing = false;
    }
  }
  PR.flush = flush;
  /* 待发的操作立刻发出去（关页面、退出软件前用） */
  PR.flushPending = async (opts = {}) => {
    if (mode !== "server") return !storageFailed;
    clearTimeout(flushT);
    if (opts.leaving && flushing && outbox.length) {
      // A closing document cannot wait to start its final request. Operations
      // are idempotent; retain the local queue for a safe replay on next open.
      return fetch(base() + "/ops", {
        method: "POST", keepalive: true,
        headers: { "Content-Type": "application/json", "X-Token": PR.token || "" },
        body: JSON.stringify({ ops: outbox.slice(), client }),
      }).then(r => r.ok).catch(() => false);
    }
    if (flushOptions) flushOptions.immediate = true;
    if (flushPromise && !await flushPromise) return false;
    // Desktop close waits for this promise; large image notes must use a normal
    // request. Keepalive has a small body quota and is only needed on page leave.
    while (outbox.length) if (!await flush({ keepalive: !!opts.leaving, immediate: true })) return false;
    return true;
  };

  async function reloadToken() {
    try {
      const data = PR.pwa ? await PR.pwa.json(base() + "/state") : await (await fetch(base() + "/state", { cache: "no-store" })).json();
      PR.token = data.token;
    } catch (e) { /* 下次重试 */ }
  }

  /* ---------- 载入 ---------- */
  PR.load = async function () {
    const embedded = document.getElementById("pr-data");
    if (embedded) {
      const d = JSON.parse(embedded.textContent);
      mode = "static";
      Object.assign(S, { paper: d.paper, web_snapshot: d.web_snapshot || "", stickerAssets: d.stickerAssets || [], mindmap: d.mindmap || null, deep_analysis: d.deep_analysis || null, discussion: d.discussion || { entries: [] }, layout: d.layout || {}, images: d.images || {}, item: d.item || {}, chat: d.chat || null, demo: d.demo || null });
      serverReader = d.reader || {};
      PR.pid = PR.pid || (S.paper.meta.source_sha256 || "paper").slice(0, 12);
    } else {
      if (!PR.pid) throw new Error(PR.t("地址里没有论文 id"));
      let d;
      if (PR.pwa) d = await PR.pwa.loadReader(PR.pid);
      else {
        const r = await fetch(base() + "/state", { cache: "no-store" });
        if (!r.ok) throw new Error(r.status === 404 ? PR.t("文献库里没有这篇论文") : PR.t("读取数据失败：{status}", { status: r.status }));
        d = await r.json();
      }
      PR.token = d.token;
      Object.assign(S, { library_id: d.library_id || PR.pwa?.scope || "", paper: d.paper, web_snapshot: d.web_snapshot || "", discussion: d.discussion, layout: d.layout || {}, item: d.item || {}, job: d.job || {}, versions: d.versions, engine: d.engine,
        figure_images_enabled: d.figure_images_enabled === true });
      serverReader = d.reader || {};
    }
    const ov = (S.item || {}).meta_override || {};
    for (const [k, v] of Object.entries(ov)) if (v) S.paper.meta[k] = v;
    if (ov.title_zh && !ov.short_zh) {
      S.paper.meta.short_zh = PR.deriveShortTitle ? PR.deriveShortTitle(ov.title_zh, true) : ov.title_zh;
    }
    outboxKey = PR.pwa?.scope ? PR.pwa.outboxKey(PR.pid) : "pr-outbox-" + PR.pid + (mode === "static" ? "-static" : "");
    PR.paperKey = PR.pid;
    outbox = PR.pwa?.scope ? PR.pwa.loadOutbox(PR.pid) : PR.ls.get(outboxKey, []);
    persistedOutbox = outbox.slice();
    rebuildReader();
    if (mode === "server" && outbox.length) { setStatus("saving", PR.t("补存上次未保存的 {n} 条修改", { n: outbox.length })); scheduleFlush(300); }
    else statusIdle();
  };

  PR.imageUrl = (rel) => ((S.paper?.meta || {}).source_type === "web" && /^https?:\/\//.test(rel) ? rel : mode === "static" ? S.images[rel] || "" : "/p/" + PR.pid + "/" + rel);
  PR.pdfUrl = (page) => (mode === "static" ? "" : "/p/" + PR.pid + "/source.pdf#page=" + page);
  PR.canAsk = () => mode === "server" && !PR.pwa?.offline && !PR.pwa?.blocked && S.engine && S.engine !== "none";

  /* ---------- 轮询：翻译方追加讨论/译文、后台翻译进度、另一个标签页改了笔记 ---------- */
  async function poll() {
    const optimized = ipad();
    if (mode !== "server" || document.hidden || PR.isPageActive?.() === false || (optimized && (ipadPolling || inputBusy()))) return;
    if (!optimized) return pollChanges(false);
    ipadPolling = true;
    try { return await pollChanges(true); } finally { ipadPolling = false; }
  }
  async function pollChanges(optimized) {
    let v;
    try {
      v = PR.pwa ? await PR.pwa.json(base() + "/versions") : await (await fetch(base() + "/versions", { cache: "no-store" })).json();
      if (PR.store.status === "offline") { if (!PR.token) await reloadToken(); flush(); if (!outbox.length) statusIdle(); }
    } catch (e) {
      if (!outbox.length) setStatus("offline", PR.t("未连上本地服务（只读）"));
      return;
    }
    if (optimized && inputBusy()) return;
    const changed = [];
    for (const name of ["paper", "discussion", "layout", "reader", "job", "item"]) if (v[name] && v[name] !== S.versions[name]) changed.push(name);
    if (typeof v.auto_translate_pending === "boolean" && v.auto_translate_pending !== !!S.job.auto_translate_pending && !changed.includes("job")) changed.push("job");
    const imagesChanged = S.figure_images_enabled !== (v.figure_images_enabled === true);
    if (!optimized || !changed.length) S.figure_images_enabled = v.figure_images_enabled === true;
    if (!changed.length) { if (imagesChanged) PR.emit("figure-images-setting"); return; }
    const staged = [];
    const apply = (name, d) => {
      S.versions[name] = d.version;
      if (name === "reader") { serverReader = d.data; rebuildReader(); } else S[name] = d.data || {};
    };
    for (const name of changed) {
      if (name === "reader" && (flushing || outbox.length)) continue; // 自己正在写，等写完
      const d = await (await fetch(base() + "/part/" + name, { cache: "no-store" })).json();
      if (optimized) staged.push([name, d]); else apply(name, d);
    }
    if (optimized) {
      await PR.ipadRuntime.wait();
      changed.length = 0;
      for (const [name, d] of staged) {
        if (name === "reader" && (flushing || outbox.length || (d.data?.rev || 0) < (serverReader?.rev || 0))) continue;
        apply(name, d); changed.push(name);
      }
      S.figure_images_enabled = v.figure_images_enabled === true;
      if (!changed.length) { if (imagesChanged) PR.emit("figure-images-setting"); return; }
    }
    if (changed.includes("paper") || changed.includes("item")) {
      const ov = (S.item || {}).meta_override || {};
      for (const [k, val] of Object.entries(ov)) if (val) S.paper.meta[k] = val;
      if (ov.title_zh && !ov.short_zh) {
        S.paper.meta.short_zh = PR.deriveShortTitle ? PR.deriveShortTitle(ov.title_zh, true) : ov.title_zh;
      }
    }
    PR.pwa?.save(PR.pid, {...S, reader: serverReader}).catch(() => {});
    PR.emit("remote", changed);
    if (imagesChanged) PR.emit("figure-images-setting");
  }
  PR.poll = poll;
  PR.startPolling = function () {
    if (mode !== "server") return;
    setInterval(poll, 2500);
    ["visibilitychange", "easyread:visibility"].forEach(event => document.addEventListener(event, () => { if (!document.hidden) poll(); }));
    window.addEventListener("focus", poll);
    window.addEventListener("online", flush);
  };

  window.addEventListener("beforeunload", (e) => {
    if (PR.captureProgress) PR.captureProgress();
    if (storageFailed && outbox.length) {
      if (mode === "server") flush();
      e.preventDefault(); e.returnValue = ""; return;
    }
    if (mode !== "server" || !outbox.length) return;
    if (outbox.some((o) => o.op !== "progress")) { flush(); e.preventDefault(); e.returnValue = ""; return; }
    flush({ keepalive: true, immediate: true });  // 只有读到哪儿没存：不拦着人走，但别丢
  });

  /* 让模型做事（回答问题、重译一段）：交给服务的小任务队列 */
  PR.ask = async function (kind, body) {
    const job = await PR.api(base() + "/" + kind, { method: "POST", body });
    PR.emit("job-started", job);
    const t = setInterval(async () => {
      const list = (await PR.api("/api/jobs?pid=" + encodeURIComponent(PR.pid))).jobs;
      const j = list.find((x) => x.id === job.id);
      if (!j || ["done", "error"].includes(j.state)) {
        clearInterval(t);
        await poll();
        PR.emit("job-finished", j || job);
      }
    }, 2000);
    return job;
  };

  /* ---------- 导出 ---------- */
  PR.exportOps = function () {
    const ops = [];
    for (const [block, e] of Object.entries(S.reader.edits || {})) if (e.zh != null) ops.push({ op: "edit", block, zh: e.zh, base: e.base, at: e.at });
    for (const n of Object.values(S.reader.notes || {})) ops.push({ op: "note", note: n, at: n.updated });
    for (const annotation of Object.values(S.reader.ai_annotations || {})) ops.push({ op: "ai_annotation", annotation, at: annotation.updated_at });
    for (const stroke of Object.values(S.reader.ink || {})) ops.push(stroke.deleted
      ? { op: "ink_del", id: stroke.id, at: stroke.updated }
      : { op: "ink", stroke, at: stroke.updated });
    for (const sticker of Object.values(S.reader.stickers || {})) ops.push(sticker.deleted
      ? { op: "sticker_del", id: sticker.id, at: sticker.updated }
      : { op: "sticker", sticker, at: sticker.updated });
    if ((S.reader.paper_note || {}).body) ops.push({ op: "paper_note", body: S.reader.paper_note.body, at: S.reader.paper_note.at });
    return { paper: PR.paperKey, exported: PR.nowIso(), ops };
  };
})(window.PR);

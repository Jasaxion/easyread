/* Progressive enhancement: private reading snapshots, not a second reader. */
(function (PR) {
  "use strict";
  if (location.protocol === "file:" || window.easyreadDesktop || document.getElementById("pr-data")) return;
  const db = globalThis.EasyReadOfflineDB;
  if (!db || !window.isSecureContext || !("serviceWorker" in navigator)) return;
  let scope = "", bound = false, blocked = false, storageEnabled = true, serial = Promise.resolve();
  let cachedReader = false, currentError = null, snapshotFailure = null;
  const offline = () => PR.connection.offline;
  const key = id => scope + ":" + id;
  const mediaName = () => "easyread-media-" + scope;
  const emit = () => PR.emit("pwa-status");
  const errorText = error => error.name === "AbortError" ? PR.t("离线操作未完成；待同步修改仍保留，请重试或导出。") : error.message;
  const fail = error => { if (!error.cancelled) { currentError = error; PR.pwa.error = errorText(error); emit(); } };
  const cancelled = () => Object.assign(new DOMException("Cancelled", "AbortError"), {cancelled: true});
  const budget = () => PR.ls.get("easyread-offline-budget", 128) * 1024 * 1024;
  const automatic = () => PR.features?.offline_reading !== false;
  const pending = id => {
    try { return Object.keys(localStorage).some(k => k.startsWith("pr-outbox-" + scope + "-" + id + "-") && JSON.parse(localStorage.getItem(k) || "[]").length); }
    catch (_) { return true; } // Corrupt/disabled storage is never permission to discard notes.
  };
  function enqueue(action) {
    const next = serial.then(() => navigator.locks ? navigator.locks.request("easyread-offline-" + scope, action) : action());
    serial = next.catch(fail); return next;
  }
  function saveSnapshot(id, data, force = false) {
    return enqueue(() => save(id, data, force)).then(value => {
      if (snapshotFailure && currentError === snapshotFailure.error && PR.pwa.error === snapshotFailure.text) {
        PR.pwa.error = ""; currentError = null; snapshotFailure = null; emit();
      }
      return value;
    }, error => { snapshotFailure = {error, text: errorText(error)}; throw error; });
  }
  async function useScope(id) {
    if (!/^[a-f0-9]{24}$/.test(id || "")) return;
    if (scope && scope !== id && (bound || downloading)) {
      blocked = true;
      throw new Error(PR.t("文献库已切换，请重新打开阅读页；未同步修改仍保留在原库。"));
    }
    scope = id; blocked = false;
    // Identity validation is independent of browser storage availability.
  }
  async function initScope() { scope = await db.get("meta", "active") || ""; }
  const ready = initScope().catch(error => { storageEnabled = false; fail(error); });
  async function json(url, options = {}) {
    const started = Date.now();
    let data;
    try { data = await PR.connection.json(url, options); }
    catch (error) {
      if (error.status === 401 || error.status === 403 || error.redirected) {
        blocked = true;
        if (scope) await db.put("meta", "locked:" + scope, Date.now()).catch(fail);
      }
      emit(); throw error;
    }
    if (data.library_id) {
      try { await useScope(data.library_id); }
      catch (error) { error.http = true; throw error; }
      try {
        await db.put("meta", "active", scope);
        if (data.token) blocked = !!await db.update("meta", "locked:" + scope, denied => denied && denied > started ? denied : false);
        storageEnabled = true;
      } catch (error) { storageEnabled = false; fail(error); }
    }
    if (data.paper && data.token && !blocked) cachedReader = false;
    emit(); return data;
  }
  function snapshot(data) {
    const clean = {};
    for (const name of ["paper", "reader", "discussion", "layout", "item", "versions"]) clean[name] = data[name] || {};
    // Never retain machine paths, model configuration, tokens or live jobs.
    clean.paper = JSON.parse(JSON.stringify(clean.paper));
    if (clean.paper.meta) delete clean.paper.meta.source_pdf_path;
    return clean;
  }
  async function entries() {
    await ready;
    if (!storageEnabled || !scope || blocked || await db.get("meta", "locked:" + scope)) return [];
    return (await db.all("papers")).filter(p => p.scope === scope);
  }
  async function bindReader(authorized = false) {
    bound = true;
    if (!scope || !navigator.serviceWorker.controller) return;
    await new Promise((resolve, reject) => {
      const channel = new MessageChannel(), timeout = setTimeout(resolve, 2000);
      channel.port1.onmessage = event => {
        clearTimeout(timeout); channel.port1.close();
        if (event.data.blocked) { blocked = true; reject(new Error(PR.t("请重新授权后同步修改"))); }
        else resolve();
      };
      navigator.serviceWorker.controller.postMessage({type: "scope", scope, authorized}, [channel.port2]);
    });
  }
  async function remove(id) {
    if (downloadingId === id) throw new Error(PR.t("已有离线保存任务，请等待或取消。"));
    if (pending(id) || (PR.pid === id && PR.store?.pending)) throw new Error(PR.t("仍有未同步修改，不能清除此文献。"));
    const cache = await caches.open(mediaName());
    // Also remove resources written before an interrupted metadata transaction.
    for (const request of await cache.keys()) if (new URL(request.url).pathname.startsWith("/p/" + id + "/")) await cache.delete(request);
    await db.del("papers", key(id)); emit();
  }
  async function trim(extra = 0, keep = "") {
    const records = await entries();
    let total = records.reduce((n, p) => n + p.bytes, 0) + extra;
    for (const p of records.sort((a,b) => a.at - b.at)) {
      if (total <= budget()) break;
      if (p.id === keep || p.pinned || pending(p.id) || p.id === PR.pid) continue;
      await remove(p.id); total -= p.bytes;
    }
    if (total > budget()) throw new Error(PR.t("离线空间不足，请清理缓存或提高容量上限；笔记未被删除。"));
  }
  function resources(id, data) {
    const urls = new Set(), prefix = "/p/" + id + "/";
    if (data.paper.meta?.source_type === "web") urls.add(prefix + "source.html");
    for (const block of data.paper.blocks || []) {
      for (const src of [block.src, block.translated_image?.src]) if (/^figures\/[A-Za-z0-9_./-]+$/.test(src || "") && !src.includes("..")) urls.add(prefix + src);
    }
    for (const match of JSON.stringify(data.reader || {}).matchAll(/\/p\/[A-Za-z0-9_-]+\/chat-media\/[a-f0-9]{32}\.png/g)) if (match[0].startsWith(prefix)) urls.add(match[0]);
    return urls;
  }
  async function save(id, data, force = false) {
    await ready;
    if (!scope) return;
    if (!storageEnabled) throw new Error(PR.t("离线快照无法保存，待同步修改仍保留；请检查浏览器存储权限。"));
    if (blocked || await db.get("meta", "locked:" + scope)) throw new Error(PR.t("请重新授权后同步修改"));
    const old = await db.get("papers", key(id));
    if (!old && !force && !automatic()) return;
    const clean = snapshot(data);
    // A late download/another tab must not replace a newer acknowledged reader.
    if ((old?.data.reader?.rev || 0) > (clean.reader.rev || 0)) {
      clean.reader = old.data.reader;
      clean.versions.reader = old.data.versions.reader;
    }
    const bytes = new Blob([JSON.stringify(clean)]).size;
    await trim(Math.max(0, bytes - (old?.dataBytes || 0)), id);
    await db.put("papers", key(id), {...old, id, scope, data: clean, dataBytes: bytes,
      bytes: bytes + (old?.mediaBytes || 0), complete: !!old?.complete && [...resources(id, clean)].every(url => old.urls?.includes(url)), at: Date.now(), title: clean.paper.meta?.title_zh || clean.paper.meta?.title_en || id});
    emit();
  }
  async function read(id) {
    await ready;
    if (!storageEnabled || !scope || blocked || await db.get("meta", "locked:" + scope)) return null;
    const record = await db.get("papers", key(id));
    if (record?.complete) {
      const cache = await caches.open(mediaName());
      const present = await Promise.all((record.urls || []).map(url => cache.match(url)));
      if (present.some(response => !response)) {
        record.complete = false;
        await db.update("papers", key(id), current => current ? {...current, complete:false} : current);
      }
    }
    return record;
  }
  async function loadReader(id) {
    await ready;
    try {
      const data = await json("/api/p/" + id + "/state");
      await bindReader(!!data.token && !blocked);
      await saveSnapshot(id, data).catch(() => {});
      return data;
    } catch (error) {
      if (error.http || error.cancelled || blocked) throw error;
      const record = await read(id);
      if (!record) throw new Error(PR.t("这篇文献尚未保存到本设备，请联网后打开。"));
      await bindReader();
      cachedReader = true; emit();
      const data = {...record.data, token: "", engine: "none", job: {}};
      const response = await (await caches.open(mediaName())).match("/p/" + id + "/source.html");
      if (response) data.web_snapshot = await response.text();
      return data;
    }
  }
  let downloading = null, downloadingId = "", downloadPromise = null;
  function requestDownload(id, pinned = true) {
    if (downloadPromise) return downloadPromise.catch(() => {}).then(() => requestDownload(id, pinned));
    PR.pwa.downloadError = "";
    downloadPromise = download(id, pinned).catch(error => {
      if (error.cancelled) return; // Only explicit cancellation, never a failed IDB transaction.
      PR.pwa.downloadError = errorText(error); emit(); throw error;
    }).finally(() => { downloadPromise = null; });
    return downloadPromise;
  }
  async function download(id, pinned = true) {
    if (downloading) throw new Error(PR.t("已有离线保存任务，请等待或取消。"));
    const controller = new AbortController(); downloading = controller; downloadingId = id; emit();
    try {
      const recent = !pinned && storageEnabled ? await read(id) : null;
      const data = recent?.data || await json("/api/p/" + id + "/state");
      await saveSnapshot(id, data, true);
      let record = await read(id);
      if (!record) return;
      await enqueue(async () => {
        record = await db.get("papers", key(id));
        if (!record) throw new Error(PR.t("这篇文献尚未保存到本设备，请联网后打开。"));
        await db.put("papers", key(id), {...record, pinned: pinned || record.pinned, complete: false});
      });
      const urls = resources(id, data);
      const cache = await caches.open(mediaName());
      let done = 0, missing = (data.paper.meta?.snapshot_external_assets || []).length;
      for (const url of urls) {
        if (controller.signal.aborted) throw cancelled();
        const cached = await cache.match(url);
        if (cached) {
          await enqueue(async () => {
            record = await db.get("papers", key(id));
            if (!record || record.urls?.includes(url)) return;
            // Reconcile a previous crash between Cache.put and metadata commit.
            const size = (await cached.blob()).size;
            await trim(size, id);
            await db.put("papers", key(id), {...record, urls:[...(record.urls || []),url], mediaBytes:(record.mediaBytes || 0)+size, bytes:record.bytes+size});
          });
        } else {
          try {
            // A slow image must not abort the entire background download.
            const resource = new AbortController(), cancel = () => resource.abort();
            controller.signal.addEventListener("abort", cancel, {once: true});
            if (controller.signal.aborted) cancel();
            const timeout = setTimeout(cancel, 60000);
            let response, blob;
            try {
              response = await fetch(url, {signal: resource.signal, cache: "default", priority: "low"});
              if (response.status === 401 || response.status === 403 || response.redirected) {
                blocked = true;
                if (scope) await db.put("meta", "locked:" + scope, Date.now()).catch(fail);
                throw Object.assign(new Error(PR.t("请重新授权后同步修改")), {http: true});
              }
              if (!response.ok) throw new Error(PR.t("请求失败 {status}", {status: response.status}));
              if (Number(response.headers.get("Content-Length")) > budget()) throw new Error("Resource exceeds offline budget");
              blob = await response.blob();
            } finally { clearTimeout(timeout); controller.signal.removeEventListener("abort", cancel); }
            await enqueue(async () => {
              if (await cache.match(url)) return; // Another tab may have completed this resource.
              await trim(blob.size, id);
              record = await db.get("papers", key(id));
              if (controller.signal.aborted) throw cancelled();
              if (!record || await db.get("meta", "locked:" + scope)) throw new Error(PR.t("请重新授权后同步修改"));
              await cache.put(url, new Response(blob, {headers: {"Content-Type": response.headers.get("Content-Type") || "application/octet-stream"}}));
              const saved = [...new Set([...(record.urls || []), url])];
              try { await db.put("papers", key(id), {...record, urls: saved, mediaBytes: (record.mediaBytes || 0) + blob.size, bytes: record.bytes + blob.size}); }
              catch (error) { await cache.delete(url); throw error; }
            });
          } catch (error) {
            if (controller.signal.aborted) throw cancelled();
            if (error.http) throw error;
            missing++;
          }
        }
        PR.pwa.progress = PR.t("正在保存资源 {done}/{total}", {done: ++done, total: urls.size}); emit();
      }
      await enqueue(async () => {
        record = await db.get("papers", key(id));
        if (record) await db.put("papers", key(id), {...record, complete: missing === 0, missing, pinned: pinned || record.pinned});
      });
      PR.pwa.downloadError = missing ? PR.t("部分资源尚未离线保存；不影响在线阅读，可稍后补齐。") : "";
      navigator.storage?.persist?.().catch(() => {});
    } finally { downloading = null; downloadingId = ""; PR.pwa.progress = ""; emit(); }
  }
  PR.pwa = {
    get downloading() { return !!downloading; },
    get scope() { return scope; }, get offline() { return offline(); }, get blocked() { return blocked; },
    get cachedReader() { return cachedReader; },
    ready, json, loadReader, read, entries, pending,
    save: saveSnapshot,
    remove: id => enqueue(() => remove(id)), download: requestDownload,
    cancel: () => downloading?.abort(),
    async rememberList(data) {
      await ready; if (!storageEnabled) return; await useScope(data.library_id);
      if (!scope) return;
      const {items, research_statuses, version} = data;
      await db.put("lists", scope, {items: items.map(({job, ...item}) => item), research_statuses, version});
    },
    async cachedList() {
      await ready;
      if (!scope || blocked || await db.get("meta", "locked:" + scope)) return null;
      const data = await db.get("lists", scope);
      const papers = await entries(), ids = new Set(papers.map(p => p.id));
      const items = (data?.items || []).filter(i => ids.has(i.id)), listed = new Set(items.map(i => i.id));
      for (const p of papers) if (!listed.has(p.id)) {
        // Direct reader links can be the first visit: do not require a prior library fetch.
        const meta = p.data.paper.meta || {}, item = p.data.item || {};
        items.push({id:p.id, title_en:meta.title_en || p.title, title_zh:meta.title_zh || "", source_type:meta.source_type || "pdf",
          authors:meta.authors || "", tags:item.tags || [], status:item.status || "reading", added:item.added || "", last_opened:item.last_opened || "",
          pages:meta.page_count || 0, done_pages:p.data.paper.translation?.done_pages?.length || 0, notes:0, highlights:0,
          has_paper_note:!!p.data.reader.paper_note?.body, progress:p.data.reader.progress?.ratio || 0});
      }
      return {...data, items, engine: "none", offline: true};
    },
    outboxKey(id) { return "pr-outbox-" + scope + "-" + id + "-"; },
    persistOutbox(id, ops) {
      try {
        // Immutable per-operation keys: one tab never rewrites another tab's queue.
        for (const op of ops) {
          op.offline_id = op.offline_id || PR.uid("op");
          const k = this.outboxKey(id) + op.offline_id;
          if (localStorage.getItem(k) === null) localStorage.setItem(k, JSON.stringify([op]));
        }
        return true;
      } catch (_) { return false; }
    },
    persistOutboxDelta(id, ops, previous) {
      const known = new Set(previous), live = new Set(ops);
      // Write replacements before removing superseded keys, including retries after partial failure.
      if (!this.persistOutbox(id, ops.filter(op => !known.has(op)))) return false;
      this.ack(id, previous.filter(op => !live.has(op)));
      return true;
    },
    loadOutbox(id) {
      const prefix = this.outboxKey(id), legacy = "pr-outbox-" + id;
      const previous = PR.ls.get(legacy, []);
      if (previous.length) {
        const owner = PR.ls.get("easyread-legacy-outbox-scope", scope);
        PR.ls.set("easyread-legacy-outbox-scope", owner);
        if (owner === scope && this.persistOutbox(id, previous)) PR.ls.del(legacy);
      }
      return Object.keys(localStorage).filter(k => k.startsWith(prefix)).flatMap(k => JSON.parse(localStorage.getItem(k) || "[]"))
        .sort((a,b) => (a.at || "").localeCompare(b.at || ""));
    },
    ack(id, batch) {
      for (const op of batch) if (op.offline_id) localStorage.removeItem(this.outboxKey(id) + op.offline_id);
    },
    async beforeLogout() {
      await ready;
      if (Object.keys(localStorage).some(k => k.startsWith("pr-outbox-" + scope + "-") && JSON.parse(localStorage.getItem(k) || "[]").length) || PR.store?.pending) throw new Error(PR.t("仍有未同步修改，请先联网保存或导出后再退出。"));
      downloading?.abort();
      if (downloadPromise) await downloadPromise.catch(() => {});
      for (const p of await entries()) await remove(p.id);
      if (scope) { await db.del("lists", scope); await caches.delete(mediaName()); await db.put("meta", "locked:" + scope, Date.now()); }
      PR.ls.del("easyread-library-list");
    },
  };
  PR.on("reader-ready", () => {
    if (!offline() && automatic()) requestDownload(PR.pid, false).catch(() => {});
  });
  PR.on("connection-status", emit);
  window.addEventListener("online", () => { PR.poll?.(); PR.lib?.load(); });
  function register() {
    navigator.serviceWorker.register("/sw.js", {scope: "/", updateViaCache: "none"}).then(reg => {
    PR.pwa.registration = reg;
    const update = () => { PR.pwa.updateWaiting = !!reg.waiting; emit(); };
    reg.addEventListener("updatefound", () => reg.installing?.addEventListener("statechange", update)); update();
    return navigator.serviceWorker.ready;
    }).then(() => { PR.pwa.shellReady = true; emit(); }).catch(fail);
  }
  // Let the current page finish its critical scripts before precaching the app.
  if (document.readyState === "complete") register();
  else window.addEventListener("load", register, {once:true});
})(window.PR);

/* Bounded requests, not retries. A slow read is not evidence of disconnection. */
(function (PR) {
  "use strict";
  let offline = navigator.onLine === false, revision = 0, probe = null;
  function update(value) {
    const changed = offline !== value;
    offline = value;
    if (changed) PR.emit("connection-status");
  }
  function reached() { revision++; update(false); }
  function failure(message, kind, extra = {}) { return Object.assign(new Error(message), {kind, ...extra}); }
  async function check(observed = revision) {
    if (observed !== revision) return offline;
    if (navigator.onLine === false) { update(true); return true; }
    if (probe) return probe;
    const checked = revision;
    probe = (async () => {
      const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 5000);
      try {
        // This endpoint is public and does not touch paper state. Even an HTTP
        // error proves reachability; authorization/server errors are separate.
        await fetch("/healthz", {cache: "no-store", signal: controller.signal});
        reached();
      } catch (_) {
        // A later successful request always outweighs this older failed probe.
        if (revision === checked) update(true);
      } finally { clearTimeout(timer); }
      return offline;
    })().finally(() => { probe = null; });
    return probe;
  }
  async function json(url, options = {}) {
    const {signal, timeout = 30000, ...request} = options;
    const controller = new AbortController(), observed = revision;
    const cancel = () => controller.abort();
    let timedOut = false, response;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeout);
    signal?.addEventListener("abort", cancel, {once: true});
    if (signal?.aborted) cancel();
    try {
      response = await fetch(url, {cache: "no-store", ...request, signal: controller.signal});
      reached();
      if (!response.ok || response.redirected) throw failure(PR.t("请求失败 {status}", {status: response.status}), "http",
        {http: true, status: response.status, redirected: response.redirected});
      // Keep the deadline through body consumption, not just response headers.
      return await response.json();
    } catch (error) {
      if (signal?.aborted) throw failure(PR.t("请求已取消"), "cancelled", {name: "AbortError", cancelled: true});
      if (error.http) throw error;
      if (!timedOut && error.name === "SyntaxError") throw failure(PR.t("服务器返回的数据无法读取，请重试"), "response", {http: true});
      if (!response) await check(observed);
      if (signal?.aborted) throw failure(PR.t("请求已取消"), "cancelled", {name: "AbortError", cancelled: true});
      throw failure(timedOut ? PR.t("请求超时，正在等待重试") : PR.t("请求未完成，请重试"), timedOut ? "timeout" : "network");
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", cancel);
    }
  }
  PR.connection = {json, check, get offline() { return offline; }};
  window.addEventListener("offline", () => { revision++; update(true); });
  window.addEventListener("online", () => { void check(); });
})(window.PR);

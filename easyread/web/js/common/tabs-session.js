/* Only local reader/workspace URLs may be restored into privileged shell frames. */
(function (global) {
  "use strict";
  const KEY = "easyread-tabs-session";
  function reader(raw, id) {
    try {
      const url = new URL(raw, location.origin);
      const match = url.pathname.match(/^\/read\/([a-zA-Z0-9_-]+)$/);
      return url.origin === location.origin && match && match[1] !== "library" && (!id || id === match[1])
        ? { id: match[1], url: url.href } : null;
    } catch (_) { return null; }
  }
  function workspace(raw, id) {
    try {
      const url = new URL(raw, location.origin);
      if (url.origin !== location.origin || !["/workspaces", "/web/workspaces.html"].includes(url.pathname)) return null;
      const project = url.searchParams.get("project") || "";
      if (project && !/^[a-z]{20}$/.test(project)) return null;
      const key = "workspace:" + (project || "home");
      return !id || id === key ? { id: key, url: url.href } : null;
    } catch (_) { return null; }
  }
  const target = (raw, id) => reader(raw, id) || workspace(raw, id);
  function library(raw) {
    try {
      const url = new URL(raw, location.origin);
      return url.origin === location.origin && ["/", "/index.html", "/research"].includes(url.pathname)
        ? url.pathname + url.search + url.hash : "/";
    } catch (_) { return "/"; }
  }
  function clean(data) {
    const seen = new Set();
    const tabs = (Array.isArray(data?.tabs) ? data.tabs : []).flatMap(tab => {
      const resolved = target(tab?.url, tab?.id);
      if (!resolved || seen.has(resolved.id)) return [];
      seen.add(resolved.id);
      return [{ ...resolved, title: String(tab.title || ""), group: typeof tab.group === "string" ? tab.group : null }];
    });
    const groups = (Array.isArray(data?.groups) ? data.groups : []).filter(g => g && typeof g.id === "string").map(g => ({
      id: g.id, title: String(g.title || g.id), color: /^#[a-f0-9]{6}$/i.test(g.color) ? g.color : "#64748b", collapsed: !!g.collapsed
    }));
    return { tabs, groups, active: seen.has(data?.active) ? data.active : "library", libraryUrl: library(data?.libraryUrl || "/") };
  }
  const storageKey = token => KEY + (token ? ":" + token : "");
  function load(token = "") {
    try { return clean(JSON.parse(sessionStorage.getItem(storageKey(token)) || "{}")); }
    catch (_) { return clean(null); }
  }
  function save(data, token = "") {
    try { sessionStorage.setItem(storageKey(token), JSON.stringify(clean(data))); } catch (_) { /* Reading still works with storage disabled. */ }
  }
  global.PRTabsSession = { reader, workspace, target, library, clean, load, save };
})(window);

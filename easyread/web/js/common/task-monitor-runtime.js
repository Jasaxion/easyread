/* One scheduler per visible page; presentation preferences never control jobs. */
(function (PR) {
  "use strict";
  if (document.getElementById?.("pr-data") || !["http:", "https:"].includes(location.protocol)) return;
  PR.createTaskMonitorRuntime = function (poll, render) {
    let timer = 0, closed = false, offline = !!PR.pwa?.offline;
    const hydrate = ["/research", "/workspaces"].includes(location.pathname) && !!PR.loadPrefs;
    let hydrating = hydrate;
    const enabled = () => PR.features?.task_notifications !== false;
    const visible = () => !closed && !document.hidden && PR.isPageActive?.() !== false;
    const presentable = () => visible() && (!PR.workspaces || enabled());
    const observable = () => !hydrating && presentable() && !PR.pwa?.offline;
    function clear() { clearTimeout(timer); timer = 0; }
    function wake() { clear(); render(); if (observable()) void poll(); }
    ["visibilitychange", "easyread:visibility"].forEach(event => document.addEventListener(event, wake));
    window.addEventListener("pagehide", () => { closed = true; clear(); });
    window.addEventListener("pageshow", () => { closed = false; wake(); });
    PR.on?.("ui-changed", wake);
    PR.on?.("pwa-status", () => {
      const next = !!PR.pwa?.offline;
      if (next !== offline) { offline = next; wake(); }
    });
    return {
      enabled, visible, presentable, observable,
      schedule(active) { clear(); if (observable()) timer = setTimeout(poll, active ? 2000 : 7000); },
      async ready() {
        try { if (hydrate) PR.useServerUi(await PR.loadPrefs()); }
        finally { hydrating = false; }
      },
    };
  };
})(window.PR);

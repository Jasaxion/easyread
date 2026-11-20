/* Reading preference only: no nested shells or redirects in offline exports. */
(function (PR) {
  "use strict";
  let navigating = false;
  function currentReader() {
    return ["/", "/index.html"].includes(location.pathname) || /^\/read\/[a-zA-Z0-9_-]+$/.test(location.pathname);
  }
  async function apply(fromSettings) {
    if (navigating || !/^https?:$/.test(location.protocol) || !currentReader() || navigator.onLine === false || PR.pwa?.offline) return;
    let shell;
    try { shell = window.parent !== window && window.parent.easyreadTabs ? window.parent : null; } catch (_) {}
    if (window.parent !== window && !shell) return;
    const enabled = PR.feature("multi_tabs"), current = location.pathname + location.search + location.hash;
    if (enabled && !shell && (fromSettings || PR.serverPrefsLoaded)) {
      navigating = true;
      await PR.flushPrefs?.();
      const args = location.pathname.startsWith("/read/") ? { open: current } : { home: "1", library: current };
      location.replace("/web/tabs.html?" + new URLSearchParams(args));
    } else if (!enabled && shell && fromSettings) {
      navigating = true;
      try {
        await PR.flushPrefs?.();
        const saved = await shell.easyreadTabs.saveAll();
        if (saved.some(ok => ok === false)) { PR.toast(PR.t("请先保存当前内容，再切换阅读模式。")); navigating = false; return; }
        shell.location.replace(current);
      } catch (_) { navigating = false; PR.toast(PR.t("请先保存当前内容，再切换阅读模式。")); }
    }
  }
  PR.on("server-ui-ready", () => { void apply(false); });
  PR.applyTabsMode = () => apply(true);
})(window.PR);

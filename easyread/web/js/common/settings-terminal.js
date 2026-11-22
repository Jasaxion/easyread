(function (PR) {
  "use strict";
  let iframe = null, opening = false, generation = 0;
  let releaseThemeFrame = null, releaseTouchFrame = null;
  let watching = null, timer = null, scanning = null, rescan = false, pageHidden = false;
  const INTERVAL = 15000;
  const T = PR.settingsTabs;
  const current = ctx => ctx === watching && ctx.s.tab === "terminal" && ctx.root.classList.contains("open");
  const visible = ctx => ctx && current(ctx) && !pageHidden && !document.hidden && PR.isPageActive?.() !== false;

  function versionLabel(item, scanned) {
    if (item.installed === false) return PR.t("未安装");
    if (item.version_error === "timeout") return PR.t("已安装，但版本查询超时");
    if (item.version_error) return PR.t("已安装，但版本查询失败");
    return item.version || (item.installed === true ? PR.t("已安装（未返回版本号）") : scanned ? PR.t("未获取检测结果") : PR.t("正在检测…"));
  }
  function cardsHtml(s) {
    return ["claude", "codex", "pi"].map(id => {
      const item = s.cliStatus?.items?.find(item => item.id === id) || { id };
      const missing = item.installed === false;
      return '<article class="cli-card"><div class="cli-avatar">' + (id === "claude" ? '✳' : id === "pi" ? 'π' : '›_') + '</div><div class="cli-card-copy"><h4>' + PR.cliName(id) + '</h4><p>' + PR.esc(versionLabel(item, !!s.cliStatus)) + '</p></div><div class="cli-card-actions">' +
        '<button class="btn sm" data-cli-action="' + id + '-login"' + (missing ? ' disabled' : '') + '>' + PR.t("登录") + '</button><button class="btn sm line" data-cli-action="' + id + '-status"' + (missing ? ' disabled' : '') + '>' + PR.t("登录状态") + '</button>' +
        (id === "pi" && missing ? '<button class="btn sm line" data-cli-action="pi-install">' + PR.t("安装") : '<button class="btn sm line" data-cli-action="' + id + '-update">' + PR.t("更新")) + '</button></div>' +
        (id === "pi" ? PR.piInstallHelp() : '') + '</article>';
    }).join('');
  }
  function scanLabel(s) {
    if (s.cliStatusLoading) return PR.t("正在检测…");
    if (s.cliStatusError) return s.cliStatus ? PR.t("检测失败，保留上次结果；请重新检测。") : PR.t("检测失败，请重试");
    return PR.t("检测当前 EasyRead 主机或容器；面板可见时自动刷新，不会重启终端。");
  }
  function paint(ctx) {
    if (!current(ctx)) return;
    const cards = ctx.root.querySelector(".cli-cards"), html = cardsHtml(ctx.s);
    // Never rerender the settings dialog or detach/reinsert the terminal iframe.
    if (cards && cards.innerHTML !== html) cards.innerHTML = html;
    const button = ctx.root.querySelector("#refreshCliStatus");
    if (button) button.disabled = !!ctx.s.cliStatusLoading;
    const status = ctx.root.querySelector("#cliScanStatus");
    if (status) {
      status.textContent = scanLabel(ctx.s);
      status.className = ctx.s.cliStatusError ? "hint err" : "hint";
      status.title = ctx.s.cliStatusError || "";
    }
  }
  function schedule(delay = INTERVAL) {
    clearTimeout(timer); timer = null;
    if (visible(watching)) timer = setTimeout(() => refresh(watching), delay);
  }
  function refresh(ctx) {
    clearTimeout(timer); timer = null;
    if (!visible(ctx)) return Promise.resolve();
    if (scanning) { rescan = true; return scanning; }
    ctx.s.cliStatusLoading = true; ctx.s.cliStatusError = ""; paint(ctx);
    scanning = Promise.resolve().then(async () => {
      try {
        const result = await PR.api("/api/terminal/status");
        if (!result || !Array.isArray(result.items)) throw new Error(PR.t("CLI 检测响应不完整，请重试。"));
        if (!current(ctx)) return;
        ctx.s.cliStatus = result;
      } catch (err) {
        if (current(ctx)) ctx.s.cliStatusError = err.message;
      } finally {
        if (current(ctx)) { ctx.s.cliStatusLoading = false; paint(ctx); }
        scanning = null;
        const delay = rescan ? 0 : INTERVAL; rescan = false; schedule(delay);
      }
    });
    return scanning;
  }
  function watch(s, root) {
    watching = { s, root }; rescan = false;
    return refresh(watching);
  }
  function wake() {
    clearTimeout(timer); timer = null;
    if (visible(watching)) refresh(watching);
  }
  window.addEventListener("focus", wake);
  window.addEventListener("pageshow", () => { pageHidden = false; wake(); });
  window.addEventListener("pagehide", () => { pageHidden = true; clearTimeout(timer); timer = null; });
  document.addEventListener("visibilitychange", wake);
  document.addEventListener("easyread:visibility", wake);

  T.terminal = {
    available: s => !!s.cfg?.embedded_terminal,
    render(s) {
      return '<div class="admin-heading"><span class="admin-icon">›_</span><div><h3>' + PR.t("CLI 与终端") + '</h3><p>' + PR.t("登录、更新、运行命令，都留在当前设置里。") + '</p></div></div><div class="cli-cards">' + cardsHtml(s) + '</div>' +
        '<div class="terminal-toolbar"><span><i class="terminal-led"></i> <span id="terminalState">' + (iframe ? PR.t("终端已打开") : PR.t("按需启动，不占用空闲会话")) + '</span></span><div><button class="btn sm line" id="refreshCliStatus">' + PR.t("重新检测") + '</button><button class="btn sm line" data-cli-action="shell">' + PR.t("打开终端") + '</button><button class="btn sm line" id="closeTerminal">' + PR.t("断开") + '</button></div></div>' +
        '<p class="hint" id="cliScanStatus" role="status">' + PR.esc(scanLabel(s)) + '</p>' +
        '<div id="terminalMount" class="terminal-mount"><div class="terminal-empty"><b>›_</b><p>' + PR.t("选择上方操作，或打开交互终端") + '</p><small>' + PR.t("CLI 登录链接可在浏览器打开；按提示粘贴验证码。") + '</small></div></div>' +
        '<p class="hint terminal-note">' + PR.t("配置和登录持久保存。关闭设置会断开当前终端，请先完成登录或更新。") + '</p>';
    },
    async mount(s, root) {
      const mount = root.querySelector("#terminalMount");
      if (iframe && iframe.parentElement !== mount) mount?.replaceChildren(iframe);
      await watch(s, root);
    },
    active: () => !!iframe,
    dispose() {
      generation++; opening = false;
      clearTimeout(timer); timer = null; watching = null; rescan = false;
      const url = iframe?.getAttribute("src");
      releaseThemeFrame?.(); releaseThemeFrame = null;
      releaseTouchFrame?.(); releaseTouchFrame = null;
      if (iframe) { iframe.src = "about:blank"; iframe.remove(); iframe = null; }
      return url ? PR.api("/api/terminal/close", { method: "POST", body: { url } }).catch(() => {}) : Promise.resolve();
    },
    async click(e, s, root) {
      if (e.target.closest("#refreshCliStatus")) {
        if (!watching || watching.s !== s || watching.root !== root) await watch(s, root);
        else await refresh(watching);
        return false;
      }
      if (e.target.closest("#closeTerminal")) { await this.dispose(); s.cliStatus = null; return true; }
      const button = e.target.closest("[data-cli-action]");
      if (!button || opening) return false;
      opening = true;
      const requestedAt = generation;
      const action = button.dataset.cliAction;
      const confirmed = !iframe || await PR.confirm({ title: PR.t("重新打开终端？"), body: PR.t("当前终端及其中未完成的命令会停止。") });
      if (!confirmed || generation !== requestedAt || s.tab !== "terminal" || !root.classList.contains("open")) {
        if (generation === requestedAt) opening = false;
        return false;
      }
      const closed = this.dispose();
      const ticket = generation;
      opening = true; button.disabled = true;
      await closed;
      if (ticket !== generation) return false;
      try {
        const result = await PR.api("/api/terminal/open", { method: "POST", body: { action } });
        if (generation !== ticket || s.tab !== "terminal" || !root.classList.contains("open")) {
          await PR.api("/api/terminal/close", { method: "POST", body: { url: result.url } }); return false;
        }
        iframe = document.createElement("iframe");
        iframe.title = PR.t("EasyRead 内嵌终端"); iframe.src = result.url;
        iframe.setAttribute("allow", "clipboard-read; clipboard-write");
        root.querySelector("#terminalMount").replaceChildren(iframe);
        releaseThemeFrame = PR.terminalTheme.register(iframe);
        releaseTouchFrame = PR.terminalTouch.register(iframe);
        root.querySelector("#terminalState").textContent = PR.t("终端已打开");
      } catch (err) { PR.toast(err.message); }
      finally {
        if (ticket === generation) {
          opening = false;
          if (s.tab === "terminal" && root.classList.contains("open")) watch(s, root);
        }
        button.disabled = false;
      }
      return false;
    },
  };
})(window.PR);

(function (PR) {
  "use strict";
  let iframe = null, opening = false, generation = 0;
  const T = PR.settingsTabs;
  T.terminal = {
    available: s => !!s.cfg?.embedded_terminal,
    render(s) {
      const cards = ["claude", "codex", "pi"].map(id => {
        const item = s.cliStatus?.items?.find(item => item.id === id) || { id };
        const missing = item.installed === false;
        return '<article class="cli-card"><div class="cli-avatar">' + (id === "claude" ? '✳' : id === "pi" ? 'π' : '›_') + '</div><div class="cli-card-copy"><h4>' + PR.cliName(id) + '</h4><p>' + PR.esc(missing ? PR.t("未安装") : item.version || PR.t("正在检测…")) + '</p></div><div class="cli-card-actions">' +
          '<button class="btn sm" data-cli-action="' + id + '-login"' + (missing ? ' disabled' : '') + '>' + PR.t("登录") + '</button><button class="btn sm line" data-cli-action="' + id + '-status"' + (missing ? ' disabled' : '') + '>' + PR.t("登录状态") + '</button>' +
          (id === "pi" && missing ? '<button class="btn sm line" data-cli-action="pi-install">' + PR.t("安装") : '<button class="btn sm line" data-cli-action="' + id + '-update">' + PR.t("更新")) + '</button></div>' +
          (id === "pi" ? PR.piInstallHelp() : '') + '</article>';
      }).join('');
      return '<div class="admin-heading"><span class="admin-icon">›_</span><div><h3>' + PR.t("CLI 与终端") + '</h3><p>' + PR.t("登录、更新、运行命令，都留在当前设置里。") + '</p></div></div><div class="cli-cards">' + cards + '</div>' +
        '<div class="terminal-toolbar"><span><i class="terminal-led"></i> <span id="terminalState">' + (iframe ? PR.t("终端已打开") : PR.t("按需启动，不占用空闲会话")) + '</span></span><div><button class="btn sm line" data-cli-action="shell">' + PR.t("打开终端") + '</button><button class="btn sm line" id="closeTerminal">' + PR.t("断开") + '</button></div></div>' +
        '<div id="terminalMount" class="terminal-mount"><div class="terminal-empty"><b>›_</b><p>' + PR.t("选择上方操作，或打开交互终端") + '</p><small>' + PR.t("CLI 登录链接可在浏览器打开；按提示粘贴验证码。") + '</small></div></div>' +
        '<p class="hint terminal-note">' + PR.t("配置和登录持久保存。关闭设置会断开当前终端，请先完成登录或更新。") + '</p>';
    },
    async mount(s, root) {
      if (iframe) root.querySelector("#terminalMount")?.replaceChildren(iframe);
      if (s.cliStatus) return;
      try {
        const result = await PR.api("/api/terminal/status");
        s.cliStatus = result;
        if (s.tab === "terminal" && root.classList.contains("open")) PR.settingsRender();
      } catch (err) { if (s.tab === "terminal") PR.toast(err.message); }
    },
    active: () => !!iframe,
    dispose() {
      generation++; opening = false;
      const url = iframe?.getAttribute("src");
      if (iframe) { iframe.src = "about:blank"; iframe.remove(); iframe = null; }
      return url ? PR.api("/api/terminal/close", { method: "POST", body: { url } }).catch(() => {}) : Promise.resolve();
    },
    async click(e, s, root) {
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
        root.querySelector("#terminalState").textContent = PR.t("终端已打开");
      } catch (err) { PR.toast(err.message); }
      finally { if (ticket === generation) opening = false; button.disabled = false; }
      return false;
    },
  };
})(window.PR);

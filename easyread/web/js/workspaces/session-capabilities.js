/* Workspace agents always use native CLI capabilities, separate from reader Q&A. */
(function (PR, W) {
  "use strict";
  W.engineName = engine => ({ codex: "Codex", claude: "Claude Code", pi: "Pi Agent" }[engine] || engine);
  W.piSafetyWarning = () => PR.t("Pi 没有内置沙箱，也不会在每次工具调用前请求批准。工具以运行服务的账号权限执行；工作目录和工具白名单不是安全隔离。");
  W.sessionCapabilityHint = (form, engine) => {
    const hint = PR.el("p", { class: "hint", role: "status" }); form.append(hint);
    const render = () => {
      hint.hidden = engine.value !== "pi";
      hint.textContent = W.piSafetyWarning() + " " + PR.t("研究空间始终使用完整原生 CLI，加载原生扩展、MCP、Skills 和工具，不受阅读问答的精简设置影响；不会自动授予项目信任或注入 EasyRead MCP。");
    };
    engine.addEventListener("change", render); render();
  };
})(window.PR, window.PR.workspaces);

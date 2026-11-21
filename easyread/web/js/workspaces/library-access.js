/* Reuse the existing full-library API/MCP and native CLI configuration.
   This panel neither reads nor stores the user's access code. */
(function (PR, W) {
  "use strict";
  const A = W.libraryAccess = {};
  A.open = () => {
    const dialog = W.modal(PR.t("远端文献 API / MCP 接入"));
    dialog.append(PR.el("p", { class: "hint", text: PR.t("在远端 Agent CLI 配置 EasyRead 的服务地址与现有完整访问授权码，即可读取整个文献库的最新正文、笔记和批注。不增加项目级限制，也不要求刷新快照。") }));
    dialog.append(PR.el("p", { class: "hint", text: PR.t("先在 EasyRead 后端启用 EASYREAD_OPEN_API=1 和 EASYREAD_MCP=1 并重启。Docker 可通过 Compose 环境变量配置；连接、SSH 转发和 CLI 登录由你在自己的终端处理。") }));
    const base = W.field(PR.t("远端可访问的 EasyRead 地址"), W.input(location.origin, { type: "url", placeholder: "https://easyread.example.com", spellcheck: "false" }), dialog);
    dialog.append(PR.el("p", { class: "hint", text: PR.t("这里的 localhost 是远端服务器自身；请使用可访问的部署地址，或自行建立标准 SSH 端口转发。此表单不接收或保存真实令牌。") }));
    const example = PR.el("pre"), error = PR.el("p", { class: "err", role: "alert" });
    const render = () => {
      try {
        const url = new URL(base.value);
        if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error();
        const endpoint = url.href.replace(/\/$/, "");
        example.textContent = JSON.stringify({ mcpServers: { easyread: {
          type: "http", url: endpoint + "/mcp", headers: { Authorization: "Bearer <EASYREAD_FULL_ACCESS_CODE>" }
        } } }, null, 2);
        error.textContent = ""; return endpoint;
      } catch (_) {
        error.textContent = PR.t("请输入不含凭据、查询参数或片段的 HTTP / HTTPS 地址。"); example.textContent = ""; return null;
      }
    };
    base.oninput = render; render();
    dialog.append(PR.el("h3", { text: PR.t("原生 HTTP MCP 配置示例") }), example,
      PR.el("p", { class: "hint", text: PR.t("将占位符替换为“设置 → 访问保护”的现有授权码，这是完整 API 的 Bearer 凭据，不是网页 X-Token。它保留现有开放 API 的全部权限，包括全库读取和导入。请放入远端 CLI 的私有配置或环境变量，不要提交到 Git 或放进项目文件。") }));
    const details = PR.el("details"), commands = PR.el("pre", { text:
      'export EASYREAD_BASE="https://easyread.example.com"\n' +
      '# Set EASYREAD_CODE securely on this host.\n' +
      'curl --fail -H "Authorization: Bearer $EASYREAD_CODE" "$EASYREAD_BASE/api/v1/info"\n' +
      'curl --fail -H "Authorization: Bearer $EASYREAD_CODE" "$EASYREAD_BASE/api/v1/documents?limit=10"\n\n' +
      '# Codex: native HTTP MCP with an environment-supplied full token\n' +
      'codex mcp add easyread --url "$EASYREAD_BASE/mcp" --bearer-token-env-var EASYREAD_CODE'
    });
    details.append(PR.el("summary", { text: PR.t("连接检查与 Codex 配置") }), commands);
    dialog.append(details, PR.el("p", { class: "hint", text: PR.t("Claude Code 可使用上面的 HTTP MCP 配置；Pi 沿用已安装的 MCP 扩展或 API Skill。把配置保存到远端服务账号的原生 CLI HOME，重新打开会话后读取。关联文献 ID 保存在项目的 .easyread-papers.json。") }), error,
      W.button(PR.t("复制配置示例"), async () => { if (render()) { await PR.copyText(example.textContent); PR.toast(PR.t("已复制")); } }, "copy", "line"));
    dialog.showModal();
  };
})(window.PR, window.PR.workspaces);

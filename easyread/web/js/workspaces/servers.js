/* SSH settings contain references to deployment-host keys, never key material. */
(function (PR, W) {
  "use strict";
  const S = W.servers = {};
  const disconnected = new Set();
  S.isDisconnected = () => disconnected.has(W.project?.remote?.server_id);
  S.disconnected = id => { disconnected.add(id); if (W.project?.remote?.server_id === id) S.connection(false); };
  S.connect = async id => {
    await W.request(endpoint(id, "connect"), {});
    disconnected.delete(id);
    if (W.project?.remote?.server_id === id) {
      S.connection(true); document.dispatchEvent(new Event("easyread:visibility"));
    }
  };
  const base = W.base + "/servers";
  const endpoint = (id, action) => base + "/" + encodeURIComponent(id) + "/" + action;
  const targetName = server => server.name + " · " + (server.user ? server.user + "@" : "") + server.host;
  S.label = project => project.remote ? PR.t("SSH · {name}", { name: project.remote.server_name || project.remote.server_id }) : PR.t("部署机本地");
  const setupLink = () => PR.el("a", { class: "btn sm line", href: base + "/setup-script", download: "easyread-remote-setup.sh", text: PR.t("下载环境配置脚本") });
  const engineSelect = () => {
    const select = PR.el("select", { class: "input", "aria-label": PR.t("待验证的 Agent") });
    ["codex", "claude", "pi"].forEach(id => select.append(PR.el("option", { value: id, text: W.engineName(id) })));
    return select;
  };
  S.report = (box, data) => {
    box.replaceChildren();
    box.append(PR.el("p", { class: data.ok ? "hint" : "err", text: data.ok ? PR.t("环境验证通过；创建时仍会重新检查。这不验证 Agent 登录、模型权限或额度。") : PR.t("环境尚未就绪，不能创建远程项目。请按下列诊断检查 SSH 配置或远端环境后重试。") }));
    const list = PR.el("ul", { class: "ws-server-checks" });
    (data.checks || []).forEach(check => {
      const row = PR.el("li", { class: check.ok ? "" : check.required === false ? "hint" : "err" });
      row.append(PR.el("strong", { text: (check.ok ? "✓ " : "✕ ") + check.name + (check.code ? " [" + check.code + "]" : "") }), document.createTextNode(" — " + (check.message || (check.ok ? PR.t("可用") : PR.t("不可用")))));
      list.append(row);
    });
    box.append(list);
  };
  S.check = async (server, engine, box) => {
    box.replaceChildren(PR.el("p", { class: "hint", text: PR.t("正在通过 SSH 检查远程环境…") }));
    try {
      const data = await W.request(endpoint(server.id, "check"), { engine });
      S.report(box, data); return data;
    } catch (error) {
      box.replaceChildren(PR.el("p", { class: "err", text: error.message }));
      return { ok: false };
    }
  };
  S.edit = async (server, refresh) => {
    const dialog = W.modal(server ? PR.t("编辑 SSH 服务器") : PR.t("添加 SSH 服务器")), form = PR.el("form");
    const fields = {};
    [
      ["name", PR.t("服务器名称"), { required: true, maxlength: 120 }],
      ["host", PR.t("SSH 配置别名、主机名或 IP"), { required: true, maxlength: 200, placeholder: "research.example.com" }],
      ["port", PR.t("SSH 端口（留空继承配置）"), { type: "number", min: 1, max: 65535 }],
      ["user", PR.t("SSH 用户（留空继承配置）"), { maxlength: 128 }],
      ["workspace", PR.t("远程 workspace 绝对目录"), { required: true, maxlength: 4096, placeholder: "/home/user/easyread-research" }],
      ["identity_file", PR.t("部署机 SSH 私钥路径（留空使用 SSH 配置）"), { maxlength: 4096, placeholder: "/home/easyread/.ssh/id_ed25519" }],
      ["python", PR.t("远端 Python 命令或绝对路径"), { required: true, maxlength: 4096, placeholder: "/home/user/.venvs/easyread/bin/python" }]
    ].forEach(([key, label, attrs]) => {
      fields[key] = W.field(label, W.input(server?.[key] ?? (key === "python" ? "python3" : ""), attrs), form);
    });
    form.append(PR.el("p", { class: "hint", text: PR.t("SSH 从 EasyRead 部署机发起，不是浏览器所在电脑。先以部署服务账号手动连接、核对主机指纹并配置密钥登录；不接收密码或私钥内容。") }));
    form.append(PR.el("p", { class: "hint", text: PR.t("读取部署服务账号的 ~/.ssh/config，支持 Host 别名和 ProxyJump。端口、用户、密钥留空时继承 SSH 配置；已填值优先，密钥仍遵循 OpenSSH 的追加规则。跳板主机须自行配置严格指纹检查和免交互认证。") }));
    form.append(PR.el("p", { class: "hint", text: PR.t("只保存连接配置，不会安装环境。下载脚本审阅后在自己的远程服务器上运行，再验证所选 Agent；远端需安装同一份 EasyRead 代码并自行登录 Agent。") }), setupLink());
    const error = PR.el("p", { class: "err", role: "alert" });
    const submit = PR.el("button", { class: "btn accent", type: "submit", text: PR.t("保存配置") });
    form.append(error, submit);
    form.onsubmit = async event => {
      event.preventDefault(); if (submit.disabled) return; submit.disabled = true; error.textContent = "";
      try {
        const body = Object.fromEntries(Object.entries(fields).map(([key, input]) => [key, key === "port" ? (input.value.trim() ? Number(input.value) : null) : input.value.trim()]));
        await W.request(server ? endpoint(server.id, "update") : base + "/create", body);
        dialog.close(); await refresh();
      } catch (e) { error.textContent = e.message; } finally { submit.disabled = false; }
    };
    dialog.append(form); dialog.showModal(); fields.name.focus();
  };
  S.open = async () => {
    const dialog = W.modal(PR.t("SSH 服务器管理")); dialog.classList.add("ws-server-dialog");
    const error = PR.el("p", { class: "err", role: "alert" }), list = PR.el("div", { class: "ws-server-list" });
    let loading = false;
    const refresh = async () => {
      if (loading) return; loading = true; error.textContent = "";
      try {
        const data = await W.request(base); if (!dialog.isConnected) return;
        list.replaceChildren();
        if (data.ssh_available === false) list.append(PR.el("p", { class: "err", text: PR.t("EasyRead 部署机未找到 OpenSSH 客户端，请先安装 ssh。") }));
        if (!data.servers?.length) list.append(W.empty(PR.t("尚未添加 SSH 服务器")));
        (data.servers || []).forEach(server => {
          const row = PR.el("article", { class: "ws-server-card" });
          row.append(PR.el("h3", { text: targetName(server) }), PR.el("code", { text: server.workspace }), PR.el("p", { class: "hint", text: server.suspended ? PR.t("SSH 已主动断开，请点击重新连接后继续。") : server.connected ? PR.t("SSH 已连接") : PR.t("按需连接；未连接不代表服务器不可用。") }));
          const controls = PR.el("div", { class: "ws-row ws-wrap" }), engine = engineSelect();
          const report = PR.el("div", { role: "status", "aria-live": "polite" });
          let checking = false;
          const check = W.button(PR.t("验证环境"), async () => {
            if (checking) return; checking = true; check.disabled = engine.disabled = true;
            try { await S.check(server, engine.value, report); }
            finally { checking = false; check.disabled = engine.disabled = false; }
          }, "check", "line");
          controls.append(engine, check, W.button(PR.t("编辑"), () => S.edit(server, refresh), "edit"));
          controls.append(W.button(PR.t("重新连接"), async () => {
            try { await S.connect(server.id); await refresh(); }
            catch (e) { error.textContent = e.message; }
          }, "sync", "line"));
          controls.append(W.button(PR.t("断开 SSH"), async () => {
            if (!await PR.confirm({ title: PR.t("断开 SSH 连接？"), body: PR.t("先停止该服务器上的 AI、目标和编译任务，再断开。主动断开后需点击重新连接；不会自动重发消息。") })) return;
            try { await W.request(endpoint(server.id, "disconnect"), {}); S.disconnected(server.id); await refresh(); }
            catch (e) { error.textContent = e.message; }
          }, "stop"));
          controls.append(W.button(PR.t("删除配置"), async () => {
            if (!await PR.confirm({ title: PR.t("删除 SSH 服务器配置？"), body: PR.t("仅删除连接配置，不清理服务器目录。仍有研究项目引用时不能删除。"), danger: true })) return;
            try { await W.request(endpoint(server.id, "delete"), {}); await refresh(); }
            catch (e) { error.textContent = e.message; }
          }, "trash"));
          row.append(controls, report); list.append(row);
        });
      } catch (e) { error.textContent = e.message; } finally { loading = false; }
    };
    const controls = PR.el("div", { class: "ws-row ws-wrap" });
    controls.append(W.button(PR.t("添加 SSH 服务器"), () => S.edit(null, refresh), "plus", "accent"), W.button(PR.t("刷新"), refresh, "sync"), setupLink(), W.button(PR.t("文献 API 接入"), () => W.libraryAccess.open(), "link"));
    dialog.append(PR.el("p", { class: "hint", text: PR.t("远程项目的文件、Beads、Git、LaTeX 和问 AI 都在所选服务器执行。SSH 连接按需复用，空闲后释放；不会回退到部署机运行。") }), controls, error, list);
    dialog.showModal(); await refresh();
  };
  // Every change of target/engine invalidates the browser gate; the server also
  // performs a fresh check at creation, so an old successful report is no grant.
  S.projectTarget = (form, engine, submit) => {
    const select = PR.el("select", { class: "input", "aria-label": PR.t("项目运行位置") });
    select.append(PR.el("option", { value: "", text: PR.t("部署机本地") }));
    W.field(PR.t("项目运行位置"), select, form);
    const tools = PR.el("div", { class: "ws-row ws-wrap" }), report = PR.el("div", { role: "status", "aria-live": "polite" });
    const hint = PR.el("p", { class: "hint" });
    let servers = [], verified = "", checking = false, creating = false, loading = false, revision = 0;
    const valid = () => !select.value || (servers.some(server => server.id === select.value) && verified === select.value + ":" + engine.value);
    const update = () => {
      submit.disabled = creating || checking || !valid();
      select.disabled = engine.disabled = creating || checking;
      check.disabled = creating || checking || loading || !servers.some(server => server.id === select.value);
      hint.textContent = select.value ? PR.t("项目和问 AI 使用远端环境及远端登录凭据。需先验证环境，创建时会再检查。") : PR.t("文件和 Agent 在 EasyRead 部署机上运行。");
    };
    const check = W.button(PR.t("验证所选服务器"), async () => {
      const server = servers.find(item => item.id === select.value); if (!server || checking) return;
      const generation = revision, key = select.value + ":" + engine.value;
      checking = true; update();
      try {
        const result = await S.check(server, engine.value, report);
        if (generation === revision && result.ok) verified = key;
      } finally { checking = false; update(); }
    }, "check", "line");
    const reload = async () => {
      if (loading || checking || creating) return;
      loading = true; verified = ""; ++revision; update();
      try {
        const data = await W.request(base), selected = select.value;
        servers = data.servers || []; select.replaceChildren(PR.el("option", { value: "", text: PR.t("部署机本地") }));
        servers.forEach(server => select.append(PR.el("option", { value: server.id, text: targetName(server) })));
        if (selected && !servers.some(server => server.id === selected)) {
          select.append(PR.el("option", { value: selected, disabled: true, text: PR.t("原服务器已不可用，请明确选择运行位置") }));
        }
        select.value = selected; report.replaceChildren();
      } catch (error) { report.replaceChildren(PR.el("p", { class: "err", text: error.message })); }
      finally { loading = false; update(); }
    };
    tools.append(check, W.button(PR.t("管理 SSH 服务器"), S.open, "gear"), W.button(PR.t("刷新服务器列表"), reload, "sync"));
    form.append(hint, tools, report);
    const apiHelp = W.button(PR.t("文献 API 接入"), () => W.libraryAccess.open(), "link", "line");
    form.append(apiHelp);
    select.addEventListener("change", () => { apiHelp.hidden = !select.value; });
    apiHelp.hidden = true;
    const invalidate = () => { ++revision; verified = ""; report.replaceChildren(); update(); };
    select.addEventListener("change", invalidate); engine.addEventListener("change", invalidate);
    update(); reload();
    return { value: () => select.value, valid, setBusy: value => { creating = value; update(); } };
  };
  S.connection = connected => {
    if (!W.project?.remote) return;
    const box = W.$("ws-remote-status"); box.hidden = false; box.classList.toggle("is-offline", !connected);
    W.$("ws-remote-target").textContent = S.label(W.project);
    W.$("ws-remote-target").title = W.workspaceRoot || "";
    W.$("ws-remote-state").textContent = S.isDisconnected() ? PR.t("SSH 已主动断开，请点击重新连接后继续。") : connected ? PR.t("文件和问 AI 均在远端运行") : PR.t("SSH 暂不可用；保留草稿，恢复后只读取状态，不重发消息。");
    W.$("ws-remote-connect").hidden = !S.isDisconnected();
  };
  S.init = () => {
    W.$("ws-servers").onclick = W.run(S.open);
    W.$("ws-remote-connect").onclick = W.run(async () => {
      const button = W.$("ws-remote-connect"); button.disabled = true;
      try { await S.connect(W.project.remote.server_id); } finally { button.disabled = false; }
    });
  };
})(window.PR, window.PR.workspaces);

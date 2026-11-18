/* Install guidance and device-local reading management. */
(function (PR) {
  "use strict";
  if (location.protocol === "file:" || window.easyreadDesktop || document.getElementById("pr-data")) return;
  const pwa = PR.pwa;
  let prompt, dialog, message, list, install, diagnostics;
  const installed = () => matchMedia("(display-mode: standalone), (display-mode: fullscreen)").matches || navigator.standalone;
  function installState() {
    if (install) {
      install.hidden = installed();
      install.textContent = prompt ? PR.t("安装应用") : PR.t("查看安装说明");
    }
    if (diagnostics) diagnostics.textContent = PR.t("访问来源：{origin}\n安全连接：{secure}\n当前窗口：{mode}\n离线服务：{worker}", {
      origin: location.origin, secure: window.isSecureContext ? PR.t("是") : PR.t("否"),
      mode: installed() ? PR.t("独立应用窗口") : PR.t("浏览器窗口"),
      worker: navigator.serviceWorker?.controller ? PR.t("已接管页面") : PR.t("尚未接管页面")
    });
  }
  window.addEventListener("beforeinstallprompt", event => { event.preventDefault(); prompt = event; installState(); });
  window.addEventListener("appinstalled", () => { prompt = null; if (install) install.hidden = true; });
  async function render() {
    if (!dialog?.open || !pwa) return;
    installState();
    list.replaceChildren();
    const entries = await pwa.entries();
    const estimate = await navigator.storage?.estimate?.().catch(() => ({})) || {};
    message.textContent = pwa.error || pwa.progress || (pwa.blocked ? PR.t("请重新授权后同步修改") : pwa.offline ? PR.t("离线阅读；修改将暂存在本设备") : pwa.updateWaiting ? PR.t("应用更新已准备好，保存笔记后关闭所有窗口再打开") : pwa.shellReady ? PR.t("应用已可离线启动") : PR.t("正在准备离线应用，请保持联网…"));
    const usage = document.getElementById("pwaUsage");
    usage.textContent = PR.t("离线正文与资源：{size} MB；站点总占用约 {total} MB", {size: (entries.reduce((n,p) => n+p.bytes, 0)/1048576).toFixed(1), total: ((estimate.usage || 0)/1048576).toFixed(1)});
    if (!entries.length) list.append(PR.el("p", {text: PR.t("还没有离线文献。联网打开正文即可自动保存。") }));
    for (const record of entries.sort((a,b) => b.at-a.at)) {
      const row = PR.el("div", {class: "pwa-record"});
      row.append(PR.el("a", {href: "/read/" + encodeURIComponent(record.id), text: record.title}));
      row.append(PR.el("small", {text: (record.pinned ? PR.t("已固定") + " · " : "") + (record.complete ? PR.t("正文与相关阅读资源已保存") : PR.t("正文已保存；部分资源尚未缓存")) + " · " + (record.bytes/1048576).toFixed(1) + " MB" + (pwa.pending(record.id) ? " · " + PR.t("待同步：请联网打开此文献") : "")}));
      for (const [label, action] of [[PR.t("补齐并固定"), () => pwa.download(record.id)], [PR.t("清除此缓存"), () => pwa.remove(record.id)]]) {
        const button = PR.el("button", {type: "button", class: "btn sm line", text: label});
        button.disabled = label === PR.t("清除此缓存") && pwa.pending(record.id);
        button.onclick = () => run(action); row.append(button);
      }
      list.append(row);
    }
  }
  async function run(action) {
    try { pwa.error = ""; await action(); } catch (error) { pwa.error = error.message; }
    await render();
  }
  function open() {
    if (!dialog) {
      dialog = PR.el("dialog", {id: "pwaDialog", "aria-labelledby": "pwaTitle"});
      dialog.append(PR.el("h2", {id: "pwaTitle", text: PR.t("安装与离线阅读")}));
      const close = PR.el("button", {class: "btn pwa-close", text: PR.t("关闭"), type: "button"});
      close.onclick = () => dialog.close(); dialog.append(close);
      const hint = !window.isSecureContext ? PR.t("手机访问需要可信 HTTPS；局域网 HTTP 不能启用离线应用。")
        : /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
          ? PR.t("在浏览器分享菜单选择“添加到主屏幕”，并以网页 App 打开。旧版 iOS 或菜单没有此项时请使用 Safari。安装后请在应用内重新检查离线文献。")
          : PR.t("在 Chrome 或 Edge 菜单中选择“安装应用”。若只提供网页快捷方式，请换用支持独立应用的浏览器版本。安装后从应用图标打开。 ");
      dialog.append(PR.el("p", {text: hint}));
      install = PR.el("button", {class: "btn accent", text: PR.t("安装应用"), type: "button"});
      install.hidden = installed();
      install.onclick = async () => {
        if (prompt) { const event = prompt; prompt = null; await event.prompt(); await event.userChoice; installState(); }
        else PR.toast(hint);
      }; dialog.append(install);
      diagnostics = PR.el("p", {class: "pwa-diagnostics"}); dialog.append(diagnostics);
      dialog.append(PR.el("p", {class: "hint", text: PR.t("Android 的 WebAPK 应能在系统“设置 → 应用”中找到并卸载；仅能删除图标可能只是快捷方式。网页无法确认系统安装包，请检查 Chrome、Google Play 服务及设备策略。") }));
      dialog.append(PR.el("p", {class: "hint", text: PR.t("独立窗口不代表独立数据沙盒。Android WebAPK 仍使用 Chrome 运行时和站点存储；iOS 使用主屏幕 Web App，不生成 Android 安装包。") }));
      dialog.append(PR.el("a", {href: "/manifest.webmanifest", target: "_blank", rel: "noopener", text: PR.t("查看应用清单")}));
      message = PR.el("p", {role: "status"}); dialog.append(message);
      if (pwa) {
        dialog.append(PR.el("p", {class: "hint", text: PR.t("保存正文、已有译文和笔记，不下载原 PDF 或原页图。离线时不能发起翻译或 AI 问答。缓存不是备份，浏览器清理可能删除本地数据。") }));
        dialog.append(PR.el("p", {id: "pwaUsage", class: "hint"}));
        const label = PR.el("label", {text: PR.t("离线缓存上限") + " "});
        const select = PR.el("select", {class: "input", "aria-label": PR.t("离线缓存上限")});
        for (const size of [64,128,256,512]) select.append(PR.el("option", {value: size, text: size + " MB"}));
        select.value = PR.ls.get("easyread-offline-budget", 128);
        select.onchange = () => PR.ls.set("easyread-offline-budget", Number(select.value));
        label.append(select); dialog.append(label);
        const automatic = PR.el("label", {class: "pwa-auto"});
        const checkbox = PR.el("input", {type: "checkbox"}); checkbox.checked = PR.features?.offline_reading !== false;
        checkbox.onchange = () => PR.applyUi({features: {...PR.features, offline_reading: checkbox.checked}}, true);
        automatic.append(checkbox, document.createTextNode(PR.t("自动保存最近阅读的正文"))); dialog.append(automatic);
        const actions = PR.el("div", {class: "pwa-actions"});
        const save = PR.el("button", {type: "button", class: "btn accent", text: PR.t("保存当前文献供离线阅读")});
        save.onclick = () => { const id = PR.pid || PR.lib?.selected; if (id) run(() => pwa.download(id)); else PR.toast(PR.t("请先选中或打开一篇文献")); };
        const cancel = PR.el("button", {type: "button", class: "btn line", text: PR.t("取消下载")}); cancel.onclick = () => pwa.cancel();
        const retry = PR.el("button", {type: "button", class: "btn line", text: PR.t("重新连接")}); retry.onclick = () => location.reload();
        actions.append(save,cancel,retry); dialog.append(actions);
        if (PR.exportOps) {
          const exportButton = PR.el("button", {type: "button", class: "btn line", text: PR.t("导出我的修改")});
          exportButton.onclick = () => {
            const url = URL.createObjectURL(new Blob([JSON.stringify(PR.exportOps(), null, 2)], {type:"application/json"}));
            const a = PR.el("a", {href:url, download:"easyread-notes-" + PR.pid + ".json"}); a.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
          }; actions.append(exportButton);
        }
        const login = PR.el("a", {href:"/login", text:PR.t("重新授权")}); dialog.append(login);
        list = PR.el("div", {class:"pwa-list"}); dialog.append(list);
        dialog.append(PR.el("p", {class:"hint", text:PR.t("更新应用时，请先保存笔记，再关闭所有应用窗口和本站标签页后重新打开。离线副本不能被服务器即时撤回，请只在可信设备上保存。") }));
      }
      document.body.append(dialog);
    }
    installState(); dialog.showModal(); render().catch(error => { message.textContent = error.message; });
  }
  PR.openPwaSettings = open;
  document.addEventListener("DOMContentLoaded", () => {
    if (pwa) {
      const status = PR.el("div", {class:"pwa-status", role:"status"}); status.hidden = true;
      document.body.append(status);
      const sizeStatus = () => document.documentElement.style.setProperty("--pwa-status-height", (status.hidden ? 0 : status.getBoundingClientRect().height) + "px");
      const updateStatus = () => {
        status.hidden = !pwa.offline && !pwa.blocked && !pwa.error && !pwa.updateWaiting;
        status.textContent = pwa.error || (pwa.blocked ? PR.t("请重新授权后同步修改") : pwa.offline ? PR.t("离线模式：仅可阅读本设备已保存的内容；修改等待同步") : PR.t("应用更新已准备好，保存笔记后关闭所有窗口再打开"));
        sizeStatus();
        render().catch(() => {});
      };
      PR.on("pwa-status", updateStatus);
      new ResizeObserver(sizeStatus).observe(status);
      updateStatus();
    }
  });
  document.addEventListener("click", event => {
    if (event.target.closest("[data-pwa-settings]")) {
      event.preventDefault();
      document.getElementById("settings")?.classList.remove("open");
      open(); return;
    }
    if (!pwa?.offline) return;
    const target = event.target.closest('a[href$=".pdf"], [data-act="page"], [data-act="pages"]');
    if (target) { event.preventDefault(); event.stopImmediatePropagation(); PR.toast(PR.t("原 PDF 和原页图未离线保存，请联网后查看。")); }
  }, true);
})(window.PR);

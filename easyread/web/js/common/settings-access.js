(function (PR) {
  "use strict";
  const T = PR.settingsTabs;
  T.access = {
    render(s) {
      const a = s.accessState;
      if (!a) return '<p class="set-lead">' + PR.t("正在读取设置…") + '</p>';
      return '<div class="admin-heading"><span class="admin-icon">◇</span><div><h3>' + PR.t("访问保护") + '</h3><p>' + PR.t("一个授权码，保护你的阅读空间。无需用户名。") + '</p></div></div>' +
        '<label class="switch-row admin-switch"><span><b>' + PR.t("启用访问授权码") + '</b><small>' + PR.t("开启后，阅读器、模型接口和终端都需要先授权") + '</small></span><input class="switch" id="accessEnabled" type="checkbox"' + (a.enabled ? ' checked' : '') + '></label>' +
        '<div class="admin-fields">' + (a.enabled ? '<label class="field"><span>' + PR.t("当前访问授权码") + '</span><input class="input" id="accessCurrent" type="password" autocomplete="current-password"></label>' : '') +
        '<label class="field"><span>' + (a.enabled ? PR.t("新的访问授权码") : PR.t("设置访问授权码")) + '</span><input class="input" id="accessNew" type="password" autocomplete="new-password" minlength="6" maxlength="256"></label>' +
        '<label class="field"><span>' + PR.t("再输入一次") + '</span><input class="input" id="accessConfirm" type="password" autocomplete="new-password" maxlength="256"></label></div>' +
        '<p class="admin-warning">' + PR.t("关闭保护后，能访问此地址的人都能使用模型和终端。仅限你信任的网络。") + '</p>' +
        '<p class="hint">' + PR.t("修改后其他浏览器会退出授权，已打开的终端会关闭。当前浏览器保持授权。") + '</p>' +
        '<div class="admin-actions"><button class="btn accent" id="saveAccess">' + PR.t("保存访问设置") + '</button>' +
        (a.enabled ? '<button class="btn line" id="logoutAccess">' + PR.t("退出当前授权") + '</button>' : '') + '</div><p id="accessSettingsMessage" role="status" class="hint"></p>';
    },
    async mount(s) {
      if (s.accessState) return;
      const a = await PR.api("/api/access");
      s.accessState = a;
      if (s.tab === "access" && PR.$("#settingsDlg").classList.contains("open")) PR.settingsRender();
    },
    async click(e, s, root) {
      const save = e.target.closest("#saveAccess"), logout = e.target.closest("#logoutAccess");
      if (!save && !logout) return false;
      const message = root.querySelector("#accessSettingsMessage");
      try {
        if (logout) {
          await PR.whiteboard?.prepareLogout();
          if (globalThis.EasyReadWhiteboardDrafts) {
            const library = await PR.api("/api/library");
            if (await globalThis.EasyReadWhiteboardDrafts.pendingForLibrary(library.library_id))
              throw new Error(PR.t("仍有未同步修改，请先联网保存或导出后再退出。"));
          }
          await PR.pwa?.beforeLogout();
          await PR.api("/api/access/logout", { method: "POST", body: {} });
          location.assign("/login"); return false;
        }
        const enabled = root.querySelector("#accessEnabled").checked;
        const code = root.querySelector("#accessNew").value;
        if (enabled && code !== root.querySelector("#accessConfirm").value) throw new Error(PR.t("两次输入的授权码不一致"));
        if (!enabled && s.accessState.enabled && !await PR.confirm({ title: PR.t("关闭访问保护？"), body: PR.t("关闭保护后，能访问此地址的人都能使用模型和终端。仅限你信任的网络。"), danger: true })) return false;
        save.disabled = true;
        const result = await PR.api("/api/access", { method: "POST", body: { enabled, code, current_code: root.querySelector("#accessCurrent")?.value || "" } });
        s.accessState = result;
        PR.settingsRender();
        PR.$("#accessSettingsMessage").textContent = PR.t("访问设置已保存");
      } catch (err) { message.textContent = err.message; message.classList.add("bad"); }
      finally { if (save) save.disabled = false; }
      return false;
    },
  };
})(window.PR);

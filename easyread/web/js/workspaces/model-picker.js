/* Native CLI suggestions, using the same select/dropdown as EasyRead settings. */
(function (PR, W) {
  "use strict";
  W.modelPicker = (form, engine, dialog) => {
    const choices = PR.el("select", { class: "input", "aria-label": PR.t("模型"), "data-searchable": "true" });
    const refresh = W.iconButton(PR.t("刷新模型列表"), () => load(true), "redo");
    const row = PR.el("div", { class: "ws-model-choice" }); row.append(choices, refresh);
    W.field(PR.t("模型"), row, form);
    const custom = W.field(PR.t("自定义模型 ID"), W.input("", { maxlength: 120, autocomplete: "off", spellcheck: "false" }), form);
    const customField = custom.parentElement;
    const hint = PR.el("p", { class: "hint ws-model-hint", role: "status" }); form.append(hint);
    const values = new Map();
    let current = engine.value, request = 0, closed = false;
    const state = () => {
      if (!values.has(current)) values.set(current, { id: "", custom: "", customMode: false, models: [] });
      return values.get(current);
    };
    function syncCustom(focus = false) {
      const value = state(); customField.hidden = !value.customMode; custom.required = value.customMode;
      custom.value = value.custom;
      if (focus && value.customMode) custom.focus();
    }
    function render() {
      const value = state(), entries = new Map();
      value.models.forEach(model => { if (typeof model.id === "string" && model.id) entries.set(model.id, model); });
      // Refreshing a directory must never silently change an already chosen ID.
      if (value.id && !entries.has(value.id)) entries.set(value.id, { id: value.id, name: value.id });
      choices.replaceChildren(PR.el("option", { value: "", text: PR.t("跟随 CLI 默认") }));
      entries.forEach(model => choices.append(PR.el("option", { value: "model:" + model.id,
        text: model.name && model.name !== model.id ? model.name + " · " + model.id : model.id,
        title: model.description || model.id })));
      const other = PR.el("option", { value: "custom", text: PR.t("自定义型号…"), "data-search-keep": "true" });
      choices.append(other);
      choices.value = value.customMode ? "custom" : value.id ? "model:" + value.id : "";
      syncCustom();
    }
    choices.onchange = () => {
      const value = state(); value.customMode = choices.value === "custom";
      if (value.customMode) {
        const query = choices.getAttribute("data-search-query") || "";
        if (query.trim()) value.custom = query.trim();
      } else value.id = choices.value.startsWith("model:") ? choices.value.slice(6) : "";
      syncCustom(true);
    };
    custom.oninput = () => { state().custom = custom.value; };
    async function load(fresh = false) {
      const id = ++request, selected = engine.value;
      current = selected; render(); choices.disabled = true; refresh.disabled = true;
      if (!["codex", "claude"].includes(selected)) { hint.textContent = PR.t("未安装"); return; }
      hint.textContent = PR.t("正在读取模型列表…");
      try {
        const data = await W.api("/models?" + new URLSearchParams({ engine: selected, ...(fresh ? { refresh: "1" } : {}) }));
        if (closed || id !== request || engine.value !== selected) return;
        state().models = Array.isArray(data.models) ? data.models : [];
        render();
        hint.textContent = data.warning && data.warning !== "availability_unverified"
          ? PR.t("部分模型目录暂不可用，可选择已有候选或自定义型号。")
          : state().models.length
            ? PR.t("可搜索选择；候选来自 CLI 目录或配置，实际可用性由账号和代理决定。")
            : PR.t("暂未获取模型列表，可使用 CLI 默认或自定义型号。");
      } catch (_) {
        if (closed || id !== request || engine.value !== selected) return;
        hint.textContent = PR.t("暂未获取模型列表，可使用 CLI 默认或自定义型号。");
      } finally {
        if (!closed && id === request) { choices.disabled = false; refresh.disabled = false; }
      }
    }
    engine.addEventListener("change", () => { void load(false); });
    dialog.addEventListener("close", () => { closed = true; ++request; }, { once: true });
    render(); void load(false);
    return { get value() { const value = state(); return value.customMode ? custom.value.trim() : value.id; } };
  };
})(window.PR, window.PR.workspaces);

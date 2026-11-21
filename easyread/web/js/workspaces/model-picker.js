/* Native CLI suggestions, using the same select/dropdown as EasyRead settings. */
(function (PR, W) {
  "use strict";
  W.modelPicker = (form, engine, dialog, initial = {}) => {
    const choices = PR.el("select", { class: "input", "aria-label": PR.t("模型"), "data-searchable": "true" });
    const refresh = W.iconButton(PR.t("刷新模型列表"), () => load(true), "redo");
    const row = PR.el("div", { class: "ws-model-choice" }); row.append(choices, refresh);
    W.field(PR.t("模型"), row, form);
    const custom = W.field(PR.t("自定义模型 ID"), W.input("", { maxlength: 120, autocomplete: "off", spellcheck: "false" }), form);
    const customField = custom.parentElement;
    const hint = PR.el("p", { class: "hint ws-model-hint", role: "status" }); form.append(hint);
    const effort = W.field(PR.t("思考强度"), PR.el("select", { class: "input", "aria-label": PR.t("思考强度") }), form);
    const names = { off: PR.t("不思考"), none: PR.t("不思考"), minimal: PR.t("最少"), low: PR.t("低"), medium: PR.t("中"), high: PR.t("高"), xhigh: PR.t("很高"), max: PR.t("最高") };
    const values = new Map();
    let current = engine.value, request = 0, closed = false;
    const state = () => {
      if (!values.has(current)) values.set(current, { id: "", custom: "", customMode: false, models: [], effort: "", levels: current === "pi" ? ["off", "minimal", "low", "medium", "high", "xhigh", "max"] : [] });
      return values.get(current);
    };
    Object.assign(state(), { id: initial.model || "", effort: initial.reasoning_effort || "" });
    function renderEffort() {
      const value = state(), model = value.models.find(item => item.id === (value.customMode ? value.custom.trim() : value.id));
      // Empty metadata is unconstrained in session_reasoning.validate; keep the engine suggestions.
      const levels = Array.isArray(model?.reasoning_levels) && model.reasoning_levels.length ? model.reasoning_levels : value.levels;
      const fallback = value.configured || model?.default_reasoning || "";
      effort.replaceChildren(PR.el("option", { value: "", text: PR.t("跟随 CLI 默认") + (fallback ? " · " + (names[fallback] || fallback) : "") }));
      [...new Set(levels)].filter(level => typeof level === "string" && level).forEach(level => {
        effort.append(PR.el("option", { value: level, text: names[level] || level }));
      });
      // Preserve unsupported or unknown current choices explicitly; the server validates writes.
      if (value.effort && !levels.includes(value.effort)) effort.append(PR.el("option", { value: value.effort,
        text: PR.t("当前值（目录未列出）：{value}", { value: value.effort }) }));
      effort.value = value.effort;
    }
    effort.onchange = () => { state().effort = effort.value; };
    function syncCustom(focus = false) {
      const value = state(); customField.hidden = !value.customMode; custom.required = value.customMode;
      custom.value = value.custom; custom.placeholder = current === "pi" ? "provider/id" : "";
      if (focus && value.customMode) custom.focus();
    }
    function render() {
      const value = state(), entries = new Map();
      value.models.forEach(model => { if (typeof model.id === "string" && model.id) entries.set(model.id, model); });
      // Refreshing a directory must never silently change an already chosen ID.
      if (value.id && !entries.has(value.id)) entries.set(value.id, { id: value.id, unlisted: true });
      choices.replaceChildren(PR.el("option", { value: "", text: PR.t("跟随 CLI 默认") }));
      entries.forEach(model => choices.append(PR.el("option", { value: "model:" + model.id,
        text: model.unlisted ? PR.t("当前值（目录未列出）：{value}", { value: model.id }) :
          model.name && model.name !== model.id ? model.name + " · " + model.id : model.id,
        title: model.description || model.id })));
      const other = PR.el("option", { value: "custom", text: PR.t("自定义型号…"), "data-search-keep": "true" });
      choices.append(other);
      choices.value = value.customMode ? "custom" : value.id ? "model:" + value.id : "";
      syncCustom(); renderEffort();
    }
    choices.onchange = () => {
      const value = state(); value.customMode = choices.value === "custom";
      if (value.customMode) {
        const query = choices.getAttribute("data-search-query") || "";
        if (query.trim()) value.custom = query.trim();
      } else value.id = choices.value.startsWith("model:") ? choices.value.slice(6) : "";
      syncCustom(true); renderEffort();
    };
    custom.oninput = () => { state().custom = custom.value; renderEffort(); };
    async function load(fresh = false) {
      const id = ++request, selected = engine.value;
      current = selected; render(); choices.disabled = true; refresh.disabled = true;
      if (!["codex", "claude", "pi"].includes(selected)) { hint.textContent = PR.t("未安装"); return; }
      hint.textContent = PR.t("正在读取模型列表…");
      try {
        const data = await W.api("/models?" + new URLSearchParams({ engine: selected, ...(fresh ? { refresh: "1" } : {}) }));
        if (closed || id !== request || engine.value !== selected) return;
        state().models = Array.isArray(data.models) ? data.models : [];
        state().levels = Array.isArray(data.reasoning_levels) && data.reasoning_levels.length ? data.reasoning_levels : selected === "pi" ? ["off", "minimal", "low", "medium", "high", "xhigh", "max"] : [];
        state().configured = typeof data.configured_reasoning === "string" ? data.configured_reasoning : "";
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
    return {
      get value() { const value = state(); return value.customMode ? custom.value.trim() : value.id; },
      get reasoningEffort() { return state().effort; }
    };
  };
})(window.PR, window.PR.workspaces);

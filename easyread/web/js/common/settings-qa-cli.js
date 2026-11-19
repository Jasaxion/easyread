/* Q&A capability pages: entry overrides are separate from CLI defaults and translation. */
(function (PR) {
  "use strict";
  const T = PR.settingsTabs;
  const defaults = engine => engine === "claude" ? ["Read", "Glob", "Grep", "WebSearch", "WebFetch"] : ["shell", "web_search", "view_image", "update_plan"];
  const root = () => PR.$("#settingsDlg");
  const copy = value => JSON.parse(JSON.stringify(value));
  const endpoint = (q, action = "") => '/api/chat/cli/' + q.engine + (q.model_id ? '/models/' + encodeURIComponent(q.model_id) : '') + action;
  const nameOf = engine => engine === "claude" ? "Claude Code" : "Codex CLI";
  let revision = 0, navigation = 0;

  function applyResponse(s, q, result) {
    if (q.model_id && (result.model_id !== q.model_id || result.engine !== q.engine)) throw new Error(PR.t("模型能力设置的身份不匹配，请重新打开设置"));
    Object.assign(q, result);
    q.initialOverrides = {...q.settings.skill_overrides};
    if (q.defaults) (s.cfg.qa_cli ||= {})[q.engine] = copy(q.defaults);
    if (q.model_id) {
      const policies = {...s.cfg.qa_model_policies};
      if (q.inherit) delete policies[q.model_id];
      s.cfg.qa_model_policies = q.inherit ? policies : {...policies, [q.model_id]:{engine:q.engine, settings:copy(q.settings)}};
    } else (s.cfg.qa_cli ||= {})[q.engine] = copy(q.settings);
  }
  PR.openQaCli = async function (engine, modelId = "") {
    if (!["claude", "codex"].includes(engine)) return;
    const s = PR.settingsState, ticket = ++navigation;
    try { await PR.flushModelSettings?.(); }
    catch (error) { PR.toast(PR.t("保存失败：{msg}", {msg:error.message})); return; }
    if (ticket !== navigation) return;
    T[s.tab]?.sync?.(s, root()); T[s.tab]?.dispose?.();
    const entry = s.chat?.models?.find(model => model.id === modelId);
    if (entry?._unsaved) {
      try {
        // Persist just this synthesized entry; do not save or alter translation settings.
        const result = await PR.api("/api/chat/models", {method:"POST", body:{append:entry}});
        if (!result.models?.some(model => model.id === modelId && model.engine === engine)) throw new Error(PR.t("找不到对应的模型条目，请先保存模型"));
        entry._unsaved = false;
      } catch (error) { PR.toast(PR.t("保存失败：{msg}", {msg:error.message})); return; }
      if (ticket !== navigation) return;
    }
    s.qaCli = {engine, model_id:modelId, model_name:entry?.name || entry?.label || modelId,
      inherit:!!modelId, loading:true, settings:null, skills:[], message:""};
    s.tab = "qa_cli"; PR.settingsRender();
    const body = root()?.querySelector?.('.set-body');
    if (body) body.scrollTop = 0;
  };
  function toggle(key, title, description, checked, disabled) {
    return '<label class="switch-row"><span><b>' + title + '</b><small>' + description + '</small></span><input class="switch" type="checkbox" data-qa="' + key + '"' + (checked ? ' checked' : '') + (disabled ? ' disabled' : '') + '></label>';
  }
  function toolsHtml(q) {
    const disabled = q.inherit || q.saving ? ' disabled' : '';
    if (q.engine === "claude") return '<label class="field"><span>' + PR.t("允许的内置工具 / 权限规则") + '</span><textarea class="input qa-tools" data-qa-tools spellcheck="false"' + disabled + ' placeholder="Read&#10;Glob&#10;Bash(git status *)">' + PR.esc(q.settings.allowed_tools.join("\n")) + '</textarea></label><p class="hint">' + PR.t("每行一个。内置工具可见性与预批准按列表设置；MCP 工具由独立开关和原生权限管理。") + '</p>';
    return '<p class="field-label">' + PR.t("允许的原生工具能力") + '</p><div class="qa-tool-options">' + q.tools.map(tool => '<label><input type="checkbox" data-qa-tool="' + tool + '"' + (q.settings.allowed_tools.includes(tool) ? ' checked' : '') + disabled + '> <code>' + tool + '</code></label>').join('') + '</div><p class="hint">' + PR.t("Codex 没有通用的 allowedTools 参数。这里只控制原生支持的能力；网页搜索和 URL 读取使用同一 Web 工具，MCP 工具另行管理。") + '</p>';
  }
  function skillsHtml(q) {
    const disabled = q.inherit || q.saving ? ' disabled' : '';
    return '<section class="qa-section"><div class="qa-section-head"><h4>' + PR.t("Skills 管理") + '</h4><div><input type="file" id="qaSkillFile" accept=".zip,.md" hidden><button class="btn sm line" data-qa-import>' + PR.t("导入 Skill") + '</button><button class="btn sm line" data-qa-refresh>' + PR.t("刷新") + '</button><button class="btn sm line" data-qa-inherit' + disabled + '>' + PR.t("恢复原生开关") + '</button></div></div>' +
      '<p class="hint">' + PR.t("导入 SKILL.md 或单个 Skill ZIP（最大 5 MiB）。导入目录仅供问答使用，不改写原生 Skills；开启 Skills 后才参与问答。") + '</p>' +
      (q.skills.length ? '<div class="qa-skills">' + q.skills.map(item => {
        const enabled = Object.hasOwn(q.settings.skill_overrides, item.id) ? q.settings.skill_overrides[item.id] : item.enabled;
        return '<label class="qa-skill"><span><b>' + PR.esc(item.name) + '</b><small>' + PR.esc(item.description) + '</small><em>' + (item.managed ? PR.t("问答专用") : PR.t("原生目录")) + '</em></span><input type="checkbox" class="switch" data-qa-skill="' + PR.esc(item.id) + '"' + (enabled ? ' checked' : '') + disabled + '></label>';
      }).join('') + '</div>' : '<div class="qa-empty">' + PR.t("暂未发现 Skills。可以导入，也可以在 CLI 终端安装后刷新。") + '</div>') +
      '<p class="hint">' + (q.engine === "codex" ? PR.t("Codex 问答专用 Skills 通过本轮技能目录提示提供；CLI 按需读取 SKILL.md，不修改全局发现目录。") : PR.t("Claude 问答专用 Skills 以临时 easyread-qa 插件加载，仅本轮有效。")) + '</p></section>';
  }
  T.qa_cli = {
    render(s) {
      const q = s.qaCli;
      if (!q) return '';
      const description = q.model_id
        ? PR.t("仅设置“{name}”的问答能力，不影响其他模型、翻译或自动处理。", {name:q.model_name || q.model_id})
        : PR.t("这是 CLI 默认问答能力，仅影响选择继承的模型，不影响翻译或自动处理。");
      const heading = '<button class="linkish qa-back" data-qa-back>← ' + PR.t("返回模型设置") + '</button><div class="admin-heading"><span class="admin-icon">⚙</span><div><h3>' + PR.esc(nameOf(q.engine) + (q.model_id ? ' · ' + q.model_name : '')) + ' · ' + PR.t("AI 问答能力") + '</h3><p>' + PR.esc(description) + '</p></div></div>' +
        (q.model_id ? '<button class="linkish" data-qa-global>' + PR.t("管理此 CLI 的默认问答能力") + '</button>' : '');
      if (q.loading) return heading + '<p class="hint">' + PR.t("正在读取 CLI 能力…") + '</p>';
      if (!q.settings) return heading + '<p class="bad">' + PR.esc(q.message) + '</p><button class="btn" data-qa-refresh>' + PR.t("重试") + '</button>';
      const p = q.settings, full = p.mode === "full", effectiveCustom = p.mode === "custom";
      const custom = !q.inherit && !q.saving && effectiveCustom, selectedMode = q.inherit ? "inherit" : p.mode;
      const modes = [...(q.model_id ? [["inherit", PR.t("继承 CLI 默认"), PR.t("使用共享默认值")]] : []),
        ["default", PR.t("默认精简"), PR.t("仅保留精简问答能力")], ["full", PR.t("完整 CLI"), PR.t("继承原生配置")], ["custom", PR.t("自定义"), PR.t("按需开放能力")]];
      return heading + '<div class="qa-modes' + (q.model_id ? ' has-inherit' : '') + '">' + modes.map(([mode,label,desc]) => '<button class="qa-mode' + (selectedMode === mode ? ' on' : '') + '" data-qa-mode="' + mode + '" aria-pressed="' + (selectedMode === mode) + '"' + (q.saving ? ' disabled' : '') + '><b>' + label + '</b><small>' + desc + '</small></button>').join('') + '</div>' +
        (full ? '<p class="admin-warning">' + PR.t("完整模式不再限制 Skill、插件、MCP 和工具，使用 CLI 原生配置与权限；不会自动绕过沙箱或权限确认。在终端修改原生设置后，新问答会读取最新配置。") + '</p>' : '') +
        '<section class="qa-section">' + toggle("skills", PR.t("开启 Skills"), PR.t("允许问答使用技能说明及其所需的已授权工具"), full || (effectiveCustom && p.skills), !custom) + toggle("mcp", PR.t("开启 MCP"), PR.t("加载 CLI 已配置的 MCP 服务；可能启动额外进程或访问外部服务"), full || (effectiveCustom && p.mcp), !custom) +
        (q.engine === "claude" ? toggle("web_search", PR.t("网页搜索"), PR.t("允许使用 WebSearch"), !effectiveCustom || p.web_search, !custom) + toggle("web_fetch", PR.t("网页读取"), PR.t("允许使用 WebFetch"), !effectiveCustom || p.web_fetch, !custom) : '') + '</section>' +
        (effectiveCustom ? '<section class="qa-section">' + toolsHtml(q) + (q.engine === "codex" ? '<label class="field"><span>' + PR.t("Codex 执行沙箱") + '</span><select class="input" data-qa="sandbox"' + (q.inherit || q.saving ? ' disabled' : '') + '>' + PR.opt([["read-only", PR.t("只读")], ["workspace-write", PR.t("允许工作目录写入")], ["inherit", PR.t("继承 CLI 原生配置")]], p.sandbox) + '</select></label>' : '') + '</section>' : '') +
        (q.model_id ? '<p class="hint">' + PR.t("CLI 原生配置和 Skill 文件仍共享；当前条目的能力开关独立保存。") + '</p>' : '') + skillsHtml(q) +
        '<section class="qa-section"><h4>' + PR.t("原生 CLI 设置") + '</h4><code class="qa-config-path">' + PR.esc(q.native.path) + '</code><p class="hint">' + PR.t("原生目录中的普通 Skills 会显示在这里；插件或符号链接请在 CLI 中管理。完整模式保留原生配置，本页的显式 Skill 开关优先。") + '</p>' +
        (s.cfg.embedded_terminal ? '<button class="btn line" data-qa-terminal>' + PR.t("打开 CLI 终端") + '</button>' : '') + '</section><div class="qa-save"><button class="btn accent" data-qa-save' + (q.saving ? ' disabled' : '') + '>' + PR.t("保存问答能力") + '</button><span role="status">' + PR.esc(q.message || '') + '</span></div>';
    },
    async mount(s) {
      const q = s.qaCli;
      if (!q?.loading || q.fetching) return;
      q.fetching = true;
      try { applyResponse(s, q, await PR.api(endpoint(q))); }
      catch (err) { q.message = err.message; }
      finally { q.loading = false; q.fetching = false; if (s.tab === 'qa_cli' && s.qaCli === q) PR.settingsRender(); }
    },
    sync(s, element) {
      const q = s.qaCli;
      if (!q?.settings || q.inherit || q.saving) return;
      element.querySelectorAll('[data-qa]').forEach(el => { if (!el.disabled) q.settings[el.dataset.qa] = el.type === 'checkbox' ? el.checked : el.value; });
      const tools = element.querySelector('[data-qa-tools]');
      if (tools) q.settings.allowed_tools = tools.value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
      const boxes = element.querySelectorAll('[data-qa-tool]');
      if (boxes.length) { q.settings.allowed_tools = [...boxes].filter(x => x.checked).map(x => x.dataset.qaTool); q.settings.web_search = q.settings.allowed_tools.includes('web_search'); }
      element.querySelectorAll('[data-qa-skill]').forEach(el => {
        const original = q.skills.find(x => x.id === el.dataset.qaSkill);
        if (!original) return;
        if (original.enabled !== el.checked) q.settings.skill_overrides[el.dataset.qaSkill] = el.checked;
        else if (Object.hasOwn(q.initialOverrides || {}, el.dataset.qaSkill)) q.settings.skill_overrides[el.dataset.qaSkill] = q.initialOverrides[el.dataset.qaSkill];
        else delete q.settings.skill_overrides[el.dataset.qaSkill];
      });
    },
    change(e, s, element) { this.sync(s, element); return false; },
    async click(e, s, element) {
      const q = s.qaCli;
      if (e.target.closest('[data-qa-back]')) { s.tab = 'chat'; return true; }
      if (e.target.closest('[data-qa-global]')) { await PR.openQaCli(q.engine); return false; }
      if (e.target.closest('[data-qa-terminal]')) { if (await this.save(s, element)) { s.tab = 'terminal'; return true; } return false; }
      if (q.saving) return false;
      if (e.target.closest('[data-qa-inherit]')) {
        if (q.inherit) return false;
        this.sync(s, element); q.settings.skill_overrides = {}; q.initialOverrides = {};
        q.skills.forEach(item => { item.enabled = item.inherited_enabled; });
        q.message = PR.t("保存后恢复原生 Skill 开关"); return true;
      }
      if (e.target.closest('[data-qa-refresh]')) { q.loading = true; q.settings = null; return true; }
      if (e.target.closest('[data-qa-save]')) { await this.save(s, element); return false; }
      const mode = e.target.closest('[data-qa-mode]');
      if (mode) {
        this.sync(s, element);
        if (mode.dataset.qaMode === 'inherit') {
          q.inherit = true; q.settings = copy(q.defaults); q.initialOverrides = {...q.settings.skill_overrides};
          q.skills.forEach(item => { item.enabled = Object.hasOwn(q.initialOverrides, item.id) ? q.initialOverrides[item.id] : item.inherited_enabled; });
          q.message = ''; return true;
        }
        if (mode.dataset.qaMode === 'full' && (q.inherit || q.settings.mode !== 'full') && !await PR.confirm({title: PR.t("开启完整问答能力？"), body: PR.t("CLI 将使用原生工具、Skills 和 MCP 配置。技能脚本或工具可能读写文件、运行命令或访问网络，请仅使用可信内容。"), ok: PR.t("开启")})) return false;
        if (mode.dataset.qaMode === 'custom' && q.settings.mode === 'default' && !q.settings.allowed_tools.length) q.settings.allowed_tools = defaults(q.engine);
        q.inherit = false; q.settings.mode = mode.dataset.qaMode; q.message = ''; return true;
      }
      if (e.target.closest('[data-qa-import]')) {
        this.sync(s, element);
        const input = element.querySelector('#qaSkillFile');
        input.onchange = async () => {
          const file = input.files[0]; if (!file) return;
          try {
            if (file.size > 5 * 1024 * 1024) throw new Error(PR.t("Skill 文件不能超过 5 MiB"));
            const reader = new FileReader();
            const encoded = await new Promise((resolve,reject) => { reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error(PR.t("读取文件失败"))); reader.readAsDataURL(file); });
            const result = await PR.api(endpoint(q, '/skills/import'), {method:'POST',body:{filename:file.name, content_b64:encoded}});
            q.skills = result.skills; q.message = PR.t("Skill 已导入；保存并开启问答 Skills 后生效");
          } catch (err) { q.message = err.message; }
          if (s.qaCli === q && s.tab === 'qa_cli') PR.settingsRender();
        };
        input.click();
      }
      return false;
    },
    async save(s, element) {
      const q = s.qaCli;
      if (q.saving || !q.settings) return;
      this.sync(s, element); q.saving = true;
      const ticket = ++revision;
      if (s.qaCli === q) PR.settingsRender();
      try {
        const result = await PR.api(endpoint(q), {method:'POST',body:q.model_id && q.inherit ? {inherit:true} : copy(q.settings)});
        applyResponse(s, q, result); q.message = PR.t("已保存，仅影响新的 AI 问答"); return true;
      } catch (err) { q.message = err.message; return false; }
      finally { q.saving = false; if (s.qaCli === q && s.tab === 'qa_cli' && ticket === revision) PR.settingsRender(); }
    },
  };
})(window.PR);

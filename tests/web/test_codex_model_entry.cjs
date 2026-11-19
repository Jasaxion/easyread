const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { test } = require("node:test");

function settings() {
  const dialog = { addEventListener() {} };
  const input = { id: "cmModel", tagName: "INPUT", value: "", dataset: {}, matches: () => false };
  const effort = { value: "" }, tier = { value: "" }, reasoning = {};
  Object.defineProperty(reasoning, "outerHTML", { set(html) {
    this.html = html;
    for (const [key, node] of [["reasoning_effort", effort], ["service_tier", tier]]) {
      const options = html.match(new RegExp('data-reasoning="' + key + '">([\\s\\S]*?)</select>'))[1];
      node.value = options.match(/value="([^"]*)" selected/)[1];
    }
  } });
  const nodes = { "#settingsDlg": dialog, "#cmModel": input, "[data-codex-reasoning]": reasoning,
    '[data-reasoning="reasoning_effort"]': effort, '[data-reasoning="service_tier"]': tier };
  const PR = { t: (s, v = {}) => s.replace(/\{(\w+)\}/g, (_, k) => v[k]),
    esc: value => String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
    icon: () => "", settingsTabs: {}, customApis: { find: () => null }, $$: () => [], $: selector => nodes[selector] || null };
  const context = { window: { PR, addEventListener() {} }, document: { addEventListener() {} },
    location: { search: "" }, URLSearchParams };
  for (const name of ["settings.js", "settings-models.js", "settings-translation.js", "settings-chat.js"]) {
    vm.runInNewContext(fs.readFileSync("easyread/web/js/common/" + name, "utf8"), context);
  }
  const state = { cfg: { engine: "codex", claude: {}, codex: {}, openai: {} },
    chat: { models: [], default: "" }, presets: [], transChecked: true,
    sources: [{ id: "codex", name: "Codex CLI", installed: true }],
    models: { codex: { default: "configured-model", models: [
      { id: "catalog-model", name: "Catalog Model", reasoning_levels: ["high", "ultra"], default_reasoning: "high" },
    ] } } };
  PR.settingsRender = () => {};
  PR.saveModelSettings = () => { state.saved = JSON.parse(JSON.stringify({
    cfg: PR.settingsTabs.engine.collect(state), chat: state.chat,
  })); };
  PR.menu = (_, items) => { state.menu = items; };
  const click = (action, i = "0") => PR.settingsTabs.chat.click({ target: {
    closest: selector => selector === "[data-cm]" ? { dataset: { cm: action, i }, getAttribute: () => "false" } : null,
  } }, state);
  const type = value => { input.value = value; return PR.settingsTabs.chat.change({ target: input }, state); };
  return { PR, state, input, effort, tier, reasoning, click, type };
}

test("Codex accepts arbitrary IDs and empty default while preserving local suggestions", () => {
  const { PR, state } = settings();
  state.cfg.codex.model = "translation-model";
  state.chat.models = [{ engine: "codex", model: "saved-model" }, { engine: "codex", model: "catalog-model" },
    { engine: "claude", model: "not-codex" }];
  const html = PR.cliModelSelect(state, "codex", "typed-model", 'id="cmModel"');
  assert.match(html, /<input[^>]*id="cmModel"[^>]*value="typed-model"/);
  assert.match(html, /placeholder="跟随默认（configured-model）"/);
  for (const id of ["catalog-model", "saved-model", "translation-model", "typed-model"]) {
    assert.match(html, new RegExp('<option value="' + id + '">'));
  }
  assert.equal((html.match(/<option value="catalog-model"/g) || []).length, 1);
  assert.doesNotMatch(html, /not-codex|<select|required/);
  assert.match(html, /留空跟随 CLI 默认/);
  assert.match(html, /未经服务商验证/);
  const blank = PR.cliModelSelect(state, "codex", "", 'id="otherModel"');
  assert.match(blank, /<input[^>]*value=""/);
  assert.notEqual(blank.match(/list="([^"]+)"/)[1], html.match(/list="([^"]+)"/)[1]);
});

test("capability navigation carries the selected entry ID and displays independent modes", async () => {
  const {PR,state,click}=settings(), calls=[];
  state.cfg.codex.model="model-a";
  state.chat.models=[{id:"a",engine:"codex",model:"model-a",name:"A"},{id:"b",engine:"codex",model:"model-b",name:"B"}];
  state.cfg.qa_cli={codex:{mode:"default"}};
  state.cfg.qa_model_policies={b:{engine:"codex",settings:{mode:"full"}}};
  PR.openQaCli=(engine,id)=>calls.push([engine,id]);
  const html=PR.settingsTabs.chat.render(state);
  assert.match(html,/问答能力：继承（默认精简）/);assert.match(html,/问答能力：完整 CLI/);
  await click("more","1");
  state.menu.find(item=>item.label==="AI 问答 · CLI 功能管理").fn();
  assert.deepEqual(calls,[["codex","b"]]);
  await click("add");
  assert.match(PR.settingsTabs.chat.render(state),/data-qa-manage="codex" disabled/);
});

test("Codex diagnostics show only known source/status labels, with escaped IDs", () => {
  const { PR, state } = settings();
  state.models.codex = { default: "configured-model", models: [{ id: "configured-model", name: "configured-model", source: "configured_default" }],
    diagnostics: [{ source: "config", status: "ok" }, { source: "models_cache", status: "missing", message: "must-not-display" }] };
  let html = PR.cliModelSelect(state, "codex", '<custom&"model>', 'id="cmModel"');
  assert.match(html, /models_cache.json · 缺失/);
  assert.match(html, /value="&lt;custom&amp;&quot;model&gt;"/);
  assert.doesNotMatch(html, /must-not-display|<custom/);
  for (const [status, label] of [["unreadable", "无法读取"], ["invalid", "格式无效"], ["empty", "名单为空"], ["filtered_empty", "无可显示的模型"]]) {
    state.models.codex.diagnostics = [{ source: "model_catalog_json", status }];
    html = PR.cliModelSelect(state, "codex", "", "");
    assert.match(html, new RegExp("model_catalog_json · " + label));
  }
  state.models.codex.diagnostics = [{ source: "unknown-sensitive-path", status: "missing" }];
  assert.doesNotMatch(PR.cliModelSelect(state, "codex", "", ""), /unknown-sensitive-path/);
  state.models.codex.models = [];
  assert.match(PR.cliModelSelect(state, "codex", "", ""), /<input/);
});

test("Claude remains a select with aliases and no Codex-only suggestions or hints", () => {
  const { PR, state } = settings();
  const html = PR.cliModelSelect(state, "claude", "sonnet", 'id="cmModel"');
  assert.match(html, /<select class="input" id="cmModel">/);
  assert.match(html, /value="sonnet" selected/);
  for (const id of ["opus", "sonnet", "haiku"]) assert.match(html, new RegExp('value="' + id + '"'));
  assert.doesNotMatch(html, /<input|datalist|codex-models|未经服务商验证/);
});

test("typed Codex cards persist for both translation and Q&A defaults, including blank", async () => {
  for (const model of ["arbitrary-provider-model", ""]) {
    const { PR, state, click, type } = settings();
    await click("add");
    assert.match(PR.settingsTabs.chat.render(state), /<input[^>]*id="cmModel"/);
    assert.equal(type("  " + model + "  "), false, "blur must not replace action buttons");
    await click("ok");
    const card = state.chat.models[0];
    assert.equal(card.model, model);
    assert.equal(card.engine, "codex");
    assert.equal(state.saved.chat.models[0].model, model);
    await click("translate");
    assert.equal(state.saved.cfg.engine, "codex");
    assert.equal(state.saved.cfg.codex.model, model);
    await click("chat-default");
    assert.equal(state.saved.chat.default, card.id);
    assert.equal(state.saved.chat.models[0].model, model);
    assert.match(PR.settingsTabs.chat.render(state), /aria-pressed="true"/);
  }
});

test("changing a Codex ID refreshes catalog reasoning without losing form focus or save clicks", async () => {
  const { PR, state, effort, tier, reasoning, click, type } = settings();
  await click("add");
  effort.value = "low"; tier.value = "fast";
  assert.equal(type("catalog-model"), false);
  assert.equal(state.form.reasoning_effort, "");
  assert.equal(state.form.service_tier, "");
  assert.match(reasoning.html, /value="ultra"/);
  assert.doesNotMatch(reasoning.html, /value="max"/);
  effort.value = "ultra"; tier.value = "fast";
  await click("ok");
  await click("translate");
  const card = state.chat.models[0];
  assert.equal(card.name, "Catalog Model");
  assert.equal(state.saved.cfg.codex.reasoning_effort, "ultra");
  await click("more");
  state.menu.find(item => item.label === "修改").fn();
  assert.equal(state.form.reasoning_effort, "ultra");
  assert.match(PR.settingsTabs.chat.render(state), /value="ultra" selected/);
  type("catalog-model");
  assert.equal(state.saved.cfg.codex.reasoning_effort, "ultra", "same ID preserves overrides");
  assert.equal(type("new-model"), false);
  assert.equal(state.saved.cfg.codex.model, "new-model");
  assert.equal(state.saved.cfg.codex.reasoning_effort, "");
  assert.equal(state.saved.cfg.codex.service_tier, "");
  assert.match(reasoning.html, /value="max"/);
  assert.doesNotMatch(reasoning.html, /value="ultra"/);
  type("");
  assert.equal(state.saved.cfg.codex.model, "");
  assert.equal(state.saved.chat.models[0].model, "");
});

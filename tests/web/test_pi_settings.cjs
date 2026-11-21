const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');

function fixture(installed = true) {
  const dialog = { addEventListener() {} }, model = { id: 'cmModel', value: '', tagName: 'INPUT', dataset: {}, matches: () => false };
  const effort = { value: '' }, command = { dataset: { k: 'pi.command' }, value: '/tools/pi', matches: () => false };
  const PR = { settingsTabs: {}, t: (s, v = {}) => s.replace(/\{(\w+)\}/g, (_, k) => v[k]),
    esc: s => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'), icon: () => '',
    $: selector => ({ '#settingsDlg': dialog, '#cmModel': model, '[data-reasoning="reasoning_effort"]': effort }[selector] || null),
    $$: selector => selector === '[data-k]' ? [command] : [], customApis: { find: () => null }, settingsRender() {} };
  const context = { window: { PR, addEventListener() {} }, document: { addEventListener() {} }, location: { search: '' }, URLSearchParams };
  for (const name of ['settings.js', 'settings-models.js', 'settings-translation.js', 'settings-chat.js']) {
    vm.runInNewContext(fs.readFileSync('easyread/web/js/common/' + name, 'utf8'), context);
  }
  vm.runInNewContext(fs.readFileSync('easyread/web/js/reader/chat-effort.js', 'utf8'), context);
  const state = { cfg: { engine: 'claude', claude: {}, codex: {}, openai: {}, pi: { command: 'pi', model: '' } },
    chat: { models: [], default: '' }, presets: [], transChecked: true,
    sources: [{ id: 'pi', name: 'Pi Agent', installed }], models: { pi: { models: [], default: '' } } };
  PR.settingsRender = () => {};
  PR.saveModelSettings = () => { state.saved = JSON.parse(JSON.stringify({ cfg: PR.settingsTabs.engine.collect(state), chat: state.chat })); };
  PR.menu = (_, items) => { state.menu = items; };
  const click = (action, i = '0') => PR.settingsTabs.chat.click({ target: { closest: selector => selector === '[data-cm]' ?
    { dataset: { cm: action, i }, getAttribute: () => 'false' } : null } }, state);
  return { PR, state, model, effort, command, click };
}

test('Pi settings support empty defaults, exact provider IDs and CLI-only suggestions, without Codex controls', async () => {
  const { PR, state, click } = fixture();
  assert.equal(PR.isCli('pi'), true); assert.equal(PR.cliName('pi'), 'Pi Agent');
  await click('add');
  let html = PR.settingsTabs.chat.render(state);
  assert.match(html, /value="pi" selected/); assert.match(html, /<input[^>]*id="cmModel"/);
  assert.match(html, /provider\/id/); assert.match(html, /data-k="pi.command"/);
  assert.doesNotMatch(html, /data-reasoning="service_tier"|gpt-|sonnet|opus/);
  assert.match(html, /npm install -g --ignore-scripts @earendil-works\/pi-coding-agent@latest/);
  assert.match(html, /data-qa-manage="pi" disabled/);
  const levels = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];
  assert.deepEqual([...PR.chatEffort.levels({ engine: 'pi' })], levels);
  for (const level of levels) assert.match(html, new RegExp('value="' + level + '"'));
  assert.equal(PR.chatEffort.label('off'), '不思考');
  assert.doesNotMatch(html, /value="none"/);
  state.models.pi.models = [{ id: 'provider/catalog', name: 'Catalog Model' }];
  state.models.codex = { models: [{ id: 'codex-only', name: 'Do not mix' }], diagnostics: [{ source: 'models_cache', status: 'missing' }] };
  state.cfg.pi.model = 'provider/saved';
  state.chat.models = [{ engine: 'pi', model: 'provider/card' }, { engine: 'codex', model: 'codex-card' }];
  html = PR.cliModelSelect(state, 'pi', '<provider/"typed>', 'id="piModel"');
  for (const id of ['provider/catalog', 'provider/saved', 'provider/card']) assert.match(html, new RegExp('value="' + id + '"'));
  assert.match(html, /&lt;provider\//); assert.match(html, /&quot;/);
  assert.doesNotMatch(html, /codex-only|codex-card|models_cache\.json/);
  assert.deepEqual([...PR.cliModelOptions({ cfg: {} }, 'pi', '')].map(o => o[0]), [''], 'no fabricated fallback models');
});

test('Pi model cards persist model/effort/command and retain separate QA policy navigation', async () => {
  for (const id of ['', 'provider/arbitrary-model']) {
    const { PR, state, click, model, effort, command } = fixture();
    await click('add'); model.value = '  ' + id + '  '; effort.value = 'off';
    assert.equal(PR.settingsTabs.chat.change({ target: model }, state), false, 'typing must not replace focused input');
    await click('ok'); await click('translate'); await click('chat-default');
    const card = state.saved.chat.models[0];
    assert.equal(card.engine, 'pi'); assert.equal(card.model, id); assert.equal(card.reasoning_effort, 'off');
    assert.equal(card.service_tier, ''); assert.equal(state.saved.chat.default, card.id);
    assert.deepEqual(state.saved.cfg.pi, { command: '/tools/pi', model: id, reasoning_effort: 'off' });
    command.value = '/other/pi'; PR.settingsTabs.chat.change({ target: command }, state);
    assert.equal(state.saved.cfg.pi.command, '/other/pi');
    state.cfg.qa_cli = { pi: { mode: 'default' } };
    assert.match(PR.settingsTabs.chat.render(state), /问答能力：继承（默认精简）/);
    state.cfg.qa_model_policies = { [card.id]: { engine: 'pi', settings: { mode: 'custom' } } };
    assert.match(PR.settingsTabs.chat.render(state), /问答能力：自定义/);
    let navigation; PR.openQaCli = (...args) => { navigation = args; };
    await click('more'); state.menu.find(item => item.label === 'AI 问答 · CLI 功能管理').fn();
    assert.deepEqual(navigation, ['pi', card.id]);
    state.menu.find(item => item.label === '修改').fn();
    assert.equal(state.form.reasoning_effort, 'off');
  }
});

test('uninstalled Pi remains a manual source with installation/login guidance without a terminal', () => {
  const { PR, state } = fixture(false);
  state.form = { kind: 'claude', model: '' }; state.editing = 'new';
  assert.match(PR.settingsTabs.chat.render(state), /value="pi">Pi Agent · 未检测到/);
  state.form = { kind: 'pi', model: '' };
  const html = PR.settingsTabs.chat.render(state);
  assert.match(html, /value="pi" selected/); assert.match(html, /Node.js ≥ 22.19/);
  assert.match(html, /\/login/); assert.match(html, /版本号不代表已登录/);
  assert.match(html, /<input[^>]*id="cmModel"/);
});

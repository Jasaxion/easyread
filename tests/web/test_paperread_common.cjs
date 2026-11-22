const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = file => fs.readFileSync(path.join(__dirname, '../../easyread/web/js/common', file), 'utf8');
const esc = value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const t = (text, vars = {}) => String(text).replace(/\{(\w+)\}/g, (_, key) => vars[key] ?? '');
test('shared markup highlights snippets safely without breaking math or current internal links', () => {
  const PR = { esc, t };
  vm.runInNewContext(read('markup.js'), { window:{PR}, URL, katex:{renderToString: tex => '<math>' + esc(tex) + '</math>'} });
  const html = PR.highlightSnippet('Attention $x+1$ <img onerror=x>', ['attention', 'x+1']);
  assert.match(html, /<mark class="rw-kw-mark">Attention<\/mark>/);
  assert.match(html, /<math>x\+1<\/math>/); assert.ok(!html.includes('<img'));
  assert.equal(PR.unlink('[source](#b-p1) [Fig](#fig:1)'), '[source](#b-p1) Fig');
  assert.equal(PR.titleText(String.raw`A \(x+y\) and H<sub>2</sub>O`), 'A x+y and H2O');
});
test('ink/sticker shortcuts avoid customized key conflicts and autohide defaults off', () => {
  const PR = { t, ls:{get:key => key === 'easyread-keys' ? {next:'w'} : {}, set() {}}, emit() {} };
  vm.runInNewContext(read('features.js'), {window:{PR}});
  assert.equal(PR.feature('autohide_tabs'), false);
  assert.equal(PR.keyAction('w'), 'next'); assert.equal(PR.keymap.inkToggle, '');
  assert.equal(PR.keyAction('r'), 'research'); assert.equal(PR.keyAction('s'), 'stickerToggle');
  PR.applyUi({features:{autohide_tabs:true}}, false); assert.equal(PR.feature('autohide_tabs'), true);
});
test('testing unsaved models preserves agy identity and URL-scoped API credentials without writing defaults', async () => {
  const calls = [];
  const PR = { t, esc, $:() => ({addEventListener() {}}), toast() {},
    apiKeyId:(id,url) => id || 'url:' + url.replace(/\/+$/, ''), apiHasKey:() => false,
    api:async (url, options) => { calls.push({url, ...options}); return {ok:true, message:'ok'}; } };
  vm.runInNewContext(read('settings.js'), {window:{PR, addEventListener() {}}, URLSearchParams, location:{protocol:'http:', search:''}, document:{addEventListener() {}}});
  const state = {presets:[], chatKeys:{'url:https://model.test/v1':'scoped-secret'}};
  await PR.testModel(state, {kind:'agy', model:'gemini-custom', reasoning_effort:'high'});
  await PR.testModel(state, {kind:'api', model:'custom', base_url:'https://model.test/v1', api:'responses'});
  assert.equal(calls[0].body.engine, 'agy'); assert.equal(calls[0].body.model, 'gemini-custom');
  assert.equal(calls[1].body.openai.api_key, 'scoped-secret'); assert.equal(calls[1].body.openai.api, 'responses');
  assert.ok(calls.every(call => call.url === '/api/config/test'));
});
test('research task monitor includes concurrent same-kind jobs and polls while idle for cross-page starts', async () => {
  const timers = [], calls = [];
  const PR = {t, esc, token:'token', emit() {}, $:() => null,
    api:async url => { calls.push(url); return url.endsWith('/monitor') ? {scope_id:'lib', tasks:[
      {id:'one', kind:'mindmap', state:'running'}, {id:'two', kind:'mindmap', state:'queued'}
    ]} : {task:null}; } };
  const context = {window:{PR, addEventListener() {}}, location:{protocol:'http:'}, document:{addEventListener() {}}, localStorage:{getItem:() => null},
    clearTimeout() {}, setTimeout:(_fn, ms) => { timers.push(ms); return 1; } };
  vm.runInNewContext(read('task-monitor-runtime.js'), context);
  vm.runInNewContext(read('research-task-monitor.js').replace('  init();\n})(window.PR);', '  render = () => {}; window.monitor = { state, taskList, schedule };\n})(window.PR);'), context);
  await PR.researchTaskMonitor.poll();
  assert.equal(context.window.monitor.taskList().length, 2);
  assert.ok(calls.includes('/api/research/v1/monitor'));
  assert.ok(calls.includes('/api/research/v2/reading-path/tasks/current'));
  assert.equal(timers.at(-1), 2000);
  Object.assign(context.window.monitor.state, {tasks:[], mindmap:null}); context.window.monitor.schedule();
  assert.equal(timers.at(-1), 7000);
});

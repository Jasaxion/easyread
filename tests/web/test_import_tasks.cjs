const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const root = path.join(__dirname, '../../easyread/web');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const t = (text, vars = {}) => String(text).replace(/\{(\w+)\}/g, (_, key) => vars[key] ?? '');
const esc = value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const tick = () => new Promise(resolve => setImmediate(resolve));
const id = n => String(n).padStart(32, '0');
const task = (n, state = 'running', extra = {}) => ({ id: id(n), kind: 'import', state, ref: 'https://paper.test/' + n, ...extra });

function harness(api, storage = new Map()) {
  const timers = new Map(), calls = [], loads = [], navigation = [], nodes = new Map();
  let timerId = 0;
  const document = { activeElement: null, addEventListener() {}, getElementById: key => nodes.get(key), body: { appendChild(node) { nodes.set(node.id, node); } } };
  class Element {
    constructor(props = {}) { Object.assign(this, { dataset: {}, hidden: false, buttons: [], isConnected: true }, props); }
    set innerHTML(html) {
      this.html = html;
      this.buttons = [...html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].map(([, attrs, text]) => {
        const button = new Element({ textContent: text, attrs: {}, disabled: /\bdisabled\b/.test(attrs) });
        for (const [, key, value] of attrs.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) button.attrs[key] = value || '';
        return button;
      });
    }
    get innerHTML() { return this.html || ''; }
    get firstElementChild() { return this.buttons[0]; }
    hasAttribute(key) { return Object.hasOwn(this.attrs || {}, key); }
    getAttribute(key) { return this.attrs?.[key]; }
    querySelectorAll(selector) { const keys = [...selector.matchAll(/\[(data-task-[\w-]+)\]/g)].map(match => match[1]); return this.buttons.filter(button => keys.some(key => button.hasAttribute(key))); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    focus() { document.activeElement = this; }
  }
  nodes.set('pr-research-task-modal', new Element({ id: 'pr-research-task-modal', hidden: true }));
  const PR = { t, esc, token: 'token', emit() {}, toast() {}, navigateWorkspace: url => navigation.push(url),
    $: selector => nodes.get(selector.slice(1)), el: (_tag, props) => new Element(props),
    lib: { load: async () => { loads.push('load'); }, select() { assert.fail('must not select'); }, openReader() { assert.fail('must not navigate automatically'); } },
    api: async (url, options) => { calls.push({ url, ...options }); return api(url, options); } };
  const context = { window: { PR, crypto: webcrypto, location: { pathname: '/' } }, document, location: { protocol: 'http:' },
    localStorage: { getItem: () => null, setItem() {} }, sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    setTimeout: (fn, ms) => { const key = ++timerId; timers.set(key, { fn, ms }); return key; }, clearTimeout: key => timers.delete(key) };
  vm.runInNewContext(read('js/common/import-tasks.js'), context);
  vm.runInNewContext(read('js/common/research-task-monitor.js').replace('  init();\n})(window.PR);', '  window.monitorTest = { taskList, state };\n})(window.PR);'), context);
  return { PR, timers, calls, loads, navigation, nodes, context, storage,
    deadline() { const timer = [...timers.values()].find(timer => timer.ms === 15000); assert.ok(timer); timer.fn(); },
    modal: () => nodes.get('pr-research-task-modal'), entry: () => nodes.get('pr-research-task-entry') };
}

test('import-tasks captures options, retains the request ID after lost responses, and accepts late success without downgrading terminal progress', async () => {
  const first = deferred(), second = deferred(); let submits = 0;
  const h = harness(() => (++submits === 1 ? first : second).promise);
  h.PR.importTasks.sync([], 'scope-a');
  const options = { ref: 'original DOI', kind: 'auto', translate: true, read: false, scope: 'range:2-4', model: 'model-a', target: 'zh', translation_extra: '<keep formulas>', pdf_export: true, pdf_output_dir: '/captured/path' };
  const firstSend = h.PR.importTasks.submit(options);
  const originalBody = { ...h.calls[0].body };
  options.ref = 'edited'; options.pdf_output_dir = '/changed';
  assert.match(originalBody.request_id, /^[a-f0-9]{32}$/);
  assert.equal(h.PR.importTasks.list()[0].state, 'submitting');
  assert.match(h.entry().innerHTML, /正在提交导入/);
  h.deadline();
  let local = h.PR.importTasks.list()[0];
  assert.equal(local.state, 'unconfirmed');
  assert.match(h.PR.importTasks.presentation(local).detailsMarkup, /original DOI/);
  assert.doesNotMatch(h.PR.importTasks.presentation(local).detailsMarkup, /文献尚未保存/);
  assert.match(h.PR.importTasks.presentation(local).detailsMarkup, /&lt;keep formulas>/);
  assert.match(h.entry().innerHTML, /导入提交状态待确认/);
  const retrySend = h.PR.importTasks.retry(local);
  assert.deepEqual({ ...h.calls[1].body }, originalBody);
  second.reject(new Error('connection lost')); await retrySend;
  assert.equal(h.PR.importTasks.list()[0].state, 'unconfirmed');
  const remote = task(1, 'failed', { request_id: originalBody.request_id, paper_id: 'saved-paper', error_reason: 'processing failed' });
  h.PR.importTasks.sync([remote], 'scope-a'); h.PR.researchTaskMonitor.refresh();
  first.resolve({ task: task(1, 'queued', { request_id: originalBody.request_id }) }); await firstSend; await tick();
  assert.equal(h.PR.importTasks.list().length, 1);
  assert.equal(h.PR.importTasks.list()[0].state, 'failed');
  assert.match(h.PR.importTasks.presentation(remote).detailsMarkup, /文献已保存，但本次处理未完成/);
  assert.equal(h.loads.length, 1);
  assert.equal(h.navigation.length, 0);
  assert.equal(JSON.parse(h.storage.get('paperread:import-submissions:scope-a')).length, 0);
  assert.ok(h.calls.every(call => call.url === '/api/import-tasks'));
});

test('import-tasks submission rejection stays retryable and poll acceptance wins over a later rejection', async () => {
  const response = deferred();
  const h = harness(() => response.promise);
  const sent = h.PR.importTasks.submit({ ref: '10.1234/test', kind: 'auto' });
  const request_id = h.calls[0].body.request_id;
  h.PR.importTasks.sync([task(2, 'completed', { request_id, paper_id: 'paper' })], 'scope');
  response.reject(new Error('response lost')); await sent;
  assert.equal(h.PR.importTasks.list().length, 1);
  assert.equal(h.PR.importTasks.list()[0].state, 'completed');
  const failed = harness(async () => { throw new Error('validation or transport error'); });
  await failed.PR.importTasks.submit({ ref: 'bad input' });
  const local = failed.PR.importTasks.list()[0];
  assert.equal(local.state, 'unconfirmed');
  assert.match(failed.PR.importTasks.presentation(local).detailsMarkup, /validation or transport error/);
  assert.match(failed.PR.importTasks.presentation(local).controls, /data-task-retry/);
});

test('import-tasks restores pending bodies only within authenticated scope and never automatically posts on reload', async () => {
  const source = harness(async () => { throw new Error('offline'); });
  source.PR.importTasks.sync([], 'scope-a');
  await source.PR.importTasks.submit({ ref: 'captured-ref', pdf_output_dir: '/original', model: 'saved' });
  const reload = harness(async () => ({ task: task(3) }), source.storage);
  reload.PR.importTasks.sync([], 'scope-b');
  assert.equal(reload.PR.importTasks.list().length, 0);
  reload.PR.importTasks.sync([], 'scope-a');
  assert.equal(reload.calls.length, 0);
  const restored = reload.PR.importTasks.list()[0];
  assert.equal(restored.state, 'unconfirmed');
  await reload.PR.importTasks.retry(restored);
  assert.deepEqual({ ...reload.calls[0].body }, { ...source.calls[0].body });
});

test('late import responses cannot leak into a newly authenticated monitor scope', async () => {
  const response = deferred();
  const h = harness(() => response.promise);
  h.PR.importTasks.sync([], 'scope-a');
  const sent = h.PR.importTasks.submit({ ref: 'private-ref' });
  h.PR.importTasks.sync([], 'scope-b');
  response.resolve({ task: task(12, 'completed', { paper_id: 'private-paper' }) });
  await sent;
  assert.equal(h.PR.importTasks.list().length, 0);
  assert.equal(h.loads.length, 0);
});

test('import ack failure keeps the completion notification until server confirmation', async () => {
  const completed = task(13, 'completed', { paper_id: 'saved' });
  const h = harness(async () => { throw new Error('ack response lost'); });
  h.PR.importTasks.sync([completed], 'scope'); h.PR.researchTaskMonitor.refresh();
  h.PR.researchTaskMonitor.show(completed);
  h.modal().querySelector('[data-task-ack]').onclick(); await tick();
  assert.equal(h.entry().hidden, false);
  assert.match(h.modal().innerHTML, /ack response lost/);
  assert.ok(h.modal().querySelector('[data-task-ack]'));
  h.PR.importTasks.sync([{ ...completed, acknowledged: true }], 'scope'); h.PR.researchTaskMonitor.refresh();
  assert.equal(h.entry().hidden, true);
});

test('shared task capsule counts mixed imports and research tasks, preserves research actions, and shows truthful import details', async () => {
  const imports = [task(4, 'running', { request: { ref: 'r', translate: true, target: 'zh' }, stage_label: '下载中', done: 2, total: 5 }), task(5, 'queued')];
  const comparison = { id: 'research', kind: 'comparison', state: 'running' };
  const h = harness(async url => url.endsWith('/monitor') ? { scope_id: 'scope', tasks: [...imports, comparison] } : { task: null });
  await h.PR.researchTaskMonitor.poll();
  assert.equal(h.context.window.monitorTest.taskList().length, 3);
  assert.match(h.entry().innerHTML, /3 项任务进行中/);
  assert.match(h.entry().innerHTML, /pr-task-entry-count">3/);
  h.PR.researchTaskMonitor.show(imports[0]);
  assert.match(h.modal().innerHTML, /导入来源/);
  assert.match(h.modal().innerHTML, /2 \/ 5/);
  assert.match(h.modal().innerHTML, /跨论文比较/);
  assert.match(h.modal().innerHTML, /取消导入/);
  assert.doesNotMatch(h.modal().innerHTML, /全文整理内容|可立即查看详细分析/);
  assert.equal(h.calls.filter(call => call.method === 'POST').length, 0);
  h.PR.researchTaskMonitor.show(comparison);
  assert.match(h.modal().innerHTML, /正在整理跨论文比较/);
  assert.match(h.modal().innerHTML, /取消分析/);
});

test('import notifications survive reload, partial PDF scheduling results open saved paper, and ack is explicit', async () => {
  const completed = task(6, 'completed', { paper_id: 'paper /6', result_state: 'partial', error_reason: 'PDF failed', stage_label: '文献已导入，PDF 导出未能启动' });
  const h = harness(async (url) => url.endsWith('/monitor') ? { scope_id: 'scope', tasks: [completed] }
    : url.endsWith('/ack') ? { task: { ...completed, acknowledged: true } } : { task: null });
  await h.PR.researchTaskMonitor.poll(); await tick();
  assert.equal(h.entry().hidden, false);
  assert.match(h.entry().innerHTML, /文献已导入，后续处理未完成/);
  h.PR.researchTaskMonitor.show(completed);
  assert.match(h.modal().innerHTML, /PDF failed/);
  assert.match(h.modal().innerHTML, /data-task-view/);
  assert.doesNotMatch(h.modal().innerHTML, /data-task-retry/);
  assert.equal(h.calls.filter(call => call.method === 'POST').length, 0);
  h.modal().querySelector('[data-task-view]').onclick(); await tick();
  assert.equal(h.navigation[0], '/read/paper%20%2F6');
  assert.equal(h.calls.at(-1).url, '/api/import-tasks/' + id(6) + '/ack');
  assert.equal(h.entry().hidden, true);
  assert.equal(h.loads.length, 1);
});

test('import cancel, failed/cancelled document access, and manual retry use import endpoints without navigation or PDF export', async () => {
  const running = task(7), cancelled = task(7, 'cancelled', { paper_id: 'existing', request: { pdf_export: true } });
  const child = task(8, 'queued', { retry_of: id(7) });
  const h = harness(async url => ({ task: url.endsWith('/cancel') ? cancelled : url.endsWith('/retry') ? child : { ...cancelled, acknowledged: true } }));
  h.PR.importTasks.sync([running], 'scope'); h.PR.researchTaskMonitor.refresh();
  h.PR.researchTaskMonitor.show(running);
  h.modal().querySelector('[data-task-cancel]').onclick(); await tick();
  assert.match(h.modal().innerHTML, /导入已取消/);
  assert.match(h.modal().innerHTML, /已有文献/);
  assert.ok(h.modal().querySelector('[data-task-view]'));
  assert.ok(h.modal().querySelector('[data-task-retry]'));
  h.modal().querySelector('[data-task-retry]').onclick(); await tick();
  assert.equal(h.context.window.monitorTest.state.dialogTask, id(8));
  assert.match(h.modal().innerHTML, /正在导入/);
  assert.equal(h.navigation.length, 0);
  assert.deepEqual(h.calls.map(call => call.url), ['/api/import-tasks/' + id(7) + '/cancel', '/api/import-tasks/' + id(7) + '/retry', '/api/import-tasks/' + id(7) + '/ack']);
});

test('ambiguous retry reconciles retry_of from monitor and an existing child is fetched instead of reposting its source', async () => {
  const lost = deferred(), source = task(9, 'failed'), child = task(10, 'failed', { retry_of: id(9) });
  const h = harness(async url => url.endsWith('/retry') ? lost.promise : { task: url.endsWith('/ack') ? { ...source, acknowledged: true } : child });
  h.PR.importTasks.sync([source], 'scope');
  const retry = h.PR.importTasks.retry(source);
  h.deadline();
  assert.match(h.PR.importTasks.presentation(source).detailsMarkup, /请求状态待确认/);
  h.PR.importTasks.sync([source, child], 'scope'); await tick();
  lost.reject(new Error('lost response')); await retry;
  assert.doesNotMatch(h.PR.importTasks.presentation(source).detailsMarkup, /lost response/);
  await h.PR.importTasks.retry({ ...source, acknowledged: true, retry_task_id: child.id });
  assert.equal(h.calls.at(-1).url, '/api/import-tasks/' + child.id);
  assert.equal(h.calls.at(-1).method, undefined);
});

test('import monitor still recovers tasks when the unrelated reading-path endpoint fails', async () => {
  const h = harness(async url => {
    if (url.endsWith('/monitor')) return { scope_id: 'scope', tasks: [task(11, 'completed', { paper_id: 'saved' })] };
    throw new Error('path unavailable');
  });
  await h.PR.researchTaskMonitor.poll();
  assert.equal(h.entry().hidden, false);
  assert.match(h.entry().innerHTML, /导入已完成/);
});

test('all monitor hosts include the common import adapter; tabs use those hosts and offline exports remain inert', () => {
  for (const page of ['library', 'reader', 'research']) {
    const html = read(page + '.html');
    assert.ok(html.indexOf('/common/import-tasks.js') >= 0, page);
    assert.ok(html.indexOf('/common/import-tasks.js') < html.indexOf('/common/research-task-monitor.js'), page);
  }
  assert.doesNotMatch(read('tabs.html'), /research-task-monitor/);
  assert.match(read('js/tabs.js'), /iframe/);
  for (const file of ['import-tasks', 'research-task-monitor']) {
    const PR = {};
    vm.runInNewContext(read('js/common/' + file + '.js'), { window: { PR }, document: { getElementById: () => ({}) } });
    assert.deepEqual(PR, {});
  }
  const PR = {};
  vm.runInNewContext(read('js/common/import-tasks.js'), { window: { PR }, document: {}, location: { protocol: 'file:' } });
  assert.deepEqual(PR, {});
});

test('library importRef closes immediately after directory capture, hands off all options, and leaves importFiles synchronous', async () => {
  const directory = deferred(), submit = deferred(), captured = [];
  let open = true;
  const classes = { contains: () => open, remove: () => { open = false; }, add() {} };
  const nodes = new Map(Object.entries({
    '#importDlg': { classList: classes, addEventListener() {}, querySelector: () => ({ value: ' extra ' }) },
    '#fileInput': { addEventListener() {}, dataset: {} }, '#importBtn': {},
    '#impKind': { value: 'pdf' }, '#impModel': { value: 'model-x' }, '#impTarget': { value: 'zh' },
    '#impPdfExport': { checked: true }, '#impPdfDirectory': { value: '' }, '#dropOverlay': {}
  }));
  const PR = { t, target: 'zh', targetName: () => '中文', lang: 'zh', $: key => nodes.get(key),
    ls: { get: () => ({ after: 'translate', scope: 'range', from: 2, to: 4 }) }, lib: { engine: 'ready' },
    pdfExport: { prepare: () => directory.promise }, importTasks: { submit: body => { assert.equal(open, false); captured.push(body); return submit.promise; } } };
  vm.runInNewContext(read('js/library/import.js'), { window: { PR, addEventListener() {} }, document: { addEventListener() {} } });
  await PR.importRef(' '); assert.equal(captured.length, 0);
  const first = PR.importRef('  original reference  ');
  await PR.importRef('duplicate while picker open');
  assert.equal(open, true); assert.equal(captured.length, 0);
  nodes.get('#impPdfDirectory').value = '/chosen'; directory.resolve(true); await tick();
  assert.equal(open, false); assert.equal(captured.length, 1);
  assert.deepEqual({ ...captured[0] }, { ref: 'original reference', kind: 'pdf', translate: true, read: false, scope: 'range:2-4', model: 'model-x', target: 'zh', pdf_export: true, pdf_output_dir: '/chosen', translation_extra: 'extra' });
  submit.resolve(); await first;
  const source = read('js/library/import.js');
  assert.doesNotMatch(source, /\/api\/import-url/);
  assert.equal((source.match(/PR\.pdfExport\.start/g) || []).length, 1);
  assert.match(source.slice(source.indexOf('async function importFiles'), source.indexOf('let preparingRef')), /PR\.pdfExport\.start/);
});

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

function harness(api, storage = new Map(), options = {}) {
  const timers = new Map(), calls = [], loads = [], navigation = [], nodes = new Map(), listeners = {}, windowEvents = {}, events = {};
  let timerId = 0, libraryScope = 'scope';
  const document = { activeElement: null, addEventListener: (name, fn) => { (listeners[name] ||= []).push(fn); }, getElementById: key => nodes.get(key), body: { appendChild(node) { nodes.set(node.id, node); } } };
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
  const PR = { t, esc, token: 'token', features: {task_notifications: options.enabled !== false},
    on: (name, fn) => { (events[name] ||= []).push(fn); }, emit: name => (events[name] || []).forEach(fn => fn()),
    toast() {}, navigateWorkspace: url => navigation.push(url),
    $: selector => nodes.get(selector.slice(1)), el: (_tag, props) => new Element(props),
    lib: { load: async () => { loads.push('load'); }, select() { assert.fail('must not select'); }, openReader() { assert.fail('must not navigate automatically'); } },
    api: async (url, options) => {
      if (url === '/api/import-tasks' && !options) return {scope_id:libraryScope,library_id:'library-id',tasks:[]};
      calls.push({ url, ...options }); return api(url, options);
    } };
  if (options.workspace) PR.workspaces = {};
  const context = { URL, window: { PR, crypto: webcrypto, location: { pathname: '/' }, addEventListener: (name, fn) => { (windowEvents[name] ||= []).push(fn); } }, document, location: { protocol: 'http:', origin: 'http://fixture.test', href: 'http://fixture.test/' },
    localStorage: { getItem: () => null, setItem() {} }, sessionStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    setTimeout: (fn, ms) => { const key = ++timerId; timers.set(key, { fn, ms }); return key; }, clearTimeout: key => timers.delete(key) };
  vm.runInNewContext(read('js/common/task-monitor-runtime.js'), context);
  vm.runInNewContext(read('js/common/activity-tasks.js'), context);
  vm.runInNewContext(read('js/common/import-tasks.js'), context);
  vm.runInNewContext(read('js/common/research-task-monitor.js').replace('  init();\n})(window.PR);', '  window.monitorTest = { taskList, state };\n})(window.PR);'), context);
  const sync = PR.importTasks.sync;
  PR.importTasks.sync = (raw, nextScope) => { if (nextScope) libraryScope = nextScope; return sync(raw, nextScope); };
  return { PR, timers, calls, loads, navigation, nodes, context, storage, document, listeners, windowEvents,
    click: button => listeners.click.forEach(fn => fn({ target: { closest: () => button } })),
    deadline() { const timer = [...timers.values()].find(timer => timer.ms === 15000); assert.ok(timer); timer.fn(); },
    modal: () => nodes.get('pr-research-task-modal'), entry: () => nodes.get('pr-research-task-entry') };
}

test('import-tasks captures options, retains the request ID after lost responses, and accepts late success without downgrading terminal progress', async () => {
  const first = deferred(), second = deferred(); let submits = 0;
  const h = harness(() => (++submits === 1 ? first : second).promise);
  h.PR.importTasks.sync([], 'scope-a');
  const options = { ref: 'original DOI', kind: 'auto', translate: true, read: false, scope: 'range:2-4', model: 'model-a', target: 'zh', translation_extra: '<keep formulas>', pdf_export: true, pdf_output_dir: '/captured/path' };
  const firstSend = h.PR.importTasks.submit(options);
  await tick();
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
  await tick();
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
  await tick();
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
  await tick();
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

test('bulk acknowledgement dismisses only completed imports and ignores repeat clicks while busy', async () => {
  const notices = [task(30, 'completed'), task(31, 'completed'), task(32, 'running'), task(33, 'failed'), task(34, 'unconfirmed'), task(35, 'completed', { acknowledged: true })];
  const responses = [deferred(), deferred()]; let count = 0;
  const h = harness(() => responses[count++].promise);
  h.PR.importTasks.sync(notices, 'scope'); h.PR.researchTaskMonitor.show(notices[0]);
  const button = h.modal().querySelector('[data-task-ack-all]');
  assert.equal(button.textContent, '完成全部通知');
  h.click(button); h.click(h.modal().querySelector('[data-task-ack-all]'));
  assert.deepEqual(h.calls.map(call => call.url), notices.slice(0, 2).map(item => '/api/import-tasks/' + item.id + '/ack'));
  responses.forEach((response, i) => response.resolve({ task: { ...notices[i], acknowledged: true } }));
  await tick();
  assert.ok(h.PR.importTasks.list().slice(0, 2).every(item => item.acknowledged));
  assert.ok(h.PR.importTasks.list().slice(2, 5).every(item => !item.acknowledged));
  assert.equal(h.modal().querySelector('[data-task-ack-all]'), null);
});

test('bulk acknowledgement leaves failed acknowledgements visible and individually retryable', async () => {
  const notices = [task(36, 'completed'), task(37, 'completed')];
  const h = harness(async url => {
    if (url.includes(id(37))) throw new Error('ack response lost');
    return { task: { ...notices[0], acknowledged: true } };
  });
  h.PR.importTasks.sync(notices, 'scope'); h.PR.researchTaskMonitor.show(notices[0]);
  h.click(h.modal().querySelector('[data-task-ack-all]')); await tick();
  assert.equal(h.PR.importTasks.list()[0].acknowledged, true);
  assert.equal(h.PR.importTasks.list()[1].acknowledged, false);
  assert.equal(h.entry().hidden, false);
  h.PR.researchTaskMonitor.show(h.PR.importTasks.list()[1]);
  assert.ok(h.modal().querySelector('[data-task-ack]'));
  assert.equal(h.modal().querySelector('[data-task-ack-all]'), null);
  assert.match(h.modal().innerHTML, /ack response lost/);
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
  assert.doesNotMatch(h.modal().innerHTML, /data-task-retry/); // No PDF request metadata in this legacy notification.
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

test('lost unbound submissions retain input without automatically posting after reload', async () => {
  const h = harness(async () => ({}));
  h.PR.api = async () => { throw new Error('network unavailable'); };
  await h.PR.importTasks.submit({ref:'retained-before-scope'});
  assert.ok(h.storage.has('paperread:import-submissions:unbound'));
  const restored = harness(async () => ({}),h.storage);
  restored.PR.importTasks.sync([], 'scope');
  assert.equal(restored.PR.importTasks.list()[0].request.ref,'retained-before-scope');
  assert.equal(restored.calls.length,0);
  await restored.PR.importTasks.ack(restored.PR.importTasks.list()[0]);
  assert.equal(restored.PR.importTasks.list().length,0);
});

test('missing tracked imports reconcile through durable status after monitor history eviction', async () => {
  const running = task(21), completed = task(21,'completed',{paper_id:'saved'});
  const h = harness(async () => ({task:completed}));
  h.PR.importTasks.sync([running],'scope');
  await h.PR.importTasks.reconcile('scope');
  assert.equal(h.PR.importTasks.list()[0].state,'completed');
  assert.ok(h.calls.some(call => call.url === '/api/import-tasks/'+running.id));
});

test('partial PDF imports expose a PDF-only retry without losing captured model and folder', () => {
  const h = harness(async()=>({}));
  const failed = task(22,'completed',{paper_id:'saved',result_state:'partial',request:{pdf_export:true,model:'chosen',pdf_output_dir:'/output'}});
  const view = h.PR.importTasks.presentation(failed);
  assert.match(view.controls,/重试 PDF 生成/);
  assert.match(view.detailsMarkup,/chosen/); assert.match(view.detailsMarkup,/\/output/);
  assert.match(view.detailsMarkup,/不会重复导入或翻译正文/);
});

test('generic activity summaries share the capsule without changing import or research actions', async () => {
  const activity = {id:'activity-chat',kind:'activity',activity_kind:'reader-chat',title:'Fixture paper',state:'running',created:2,url:'/read/paper'};
  const imported = task(50,'running');
  const h=harness(async url=>url.endsWith('/monitor')?{scope_id:'scope',token:'fresh',tasks:[imported],activities:[activity]}:{task:null});
  await h.PR.researchTaskMonitor.poll();assert.equal(h.PR.token,'fresh');
  assert.equal(h.context.window.monitorTest.taskList().length,2);assert.match(h.entry().innerHTML,/2 项任务进行中/);
  h.PR.researchTaskMonitor.show(h.PR.activityTasks.list()[0]);
  assert.match(h.modal().innerHTML,/AI 问答/);assert.match(h.modal().innerHTML,/Fixture paper/);
  assert.equal(h.modal().querySelector('[data-task-cancel]'),null);assert.equal(h.modal().querySelector('[data-task-retry]'),null);
  h.modal().querySelector('[data-task-view]').onclick();assert.equal(h.navigation[0],'/read/paper');
  assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
});

test('activity acknowledgement is confirmed separately, rejects active jobs and retains failures', async()=>{
  const activity={id:'activity-test',kind:'activity',activity_kind:'workspace-session',title:'Fixture project',state:'failed',url:'/workspaces?project=test'};
  let succeeds=false;
  const h=harness(async()=>{if(!succeeds)throw Error('offline');return {task:{...activity,acknowledged:true}};});
  h.PR.activityTasks.sync([activity],'scope');h.PR.researchTaskMonitor.refresh();
  const record=h.PR.activityTasks.list()[0];await h.PR.activityTasks.ack({...record,state:'running'});assert.equal(h.calls.length,0);
  await h.PR.activityTasks.ack(record);assert.equal(record.acknowledged,false);assert.equal(h.entry().hidden,false);
  succeeds=true;await h.PR.activityTasks.ack(record);assert.equal(record.acknowledged,true);
  assert.equal(h.calls.at(-1).url,'/api/research/v1/activities/activity-test/ack');assert.equal(h.calls.at(-1).body.scope_id,'scope');
  h.PR.activityTasks.sync([activity],'scope');assert.equal(h.PR.activityTasks.list()[0].acknowledged,true,'late GET must not undo confirmed acknowledgement');
  h.PR.activityTasks.sync([], 'other');assert.equal(h.PR.activityTasks.list().length,0);
  h.PR.activityTasks.open({...activity,url:'https://untrusted.test/read/paper'});assert.equal(h.navigation.length,0);
});

test('complete-all reuses existing acknowledgement paths across imports, research and activity notices',async()=>{
  const imported=task(52,'completed'),research={id:'research-done',kind:'comparison',state:'completed'};
  const activity={id:'activity-done',kind:'activity',activity_kind:'reader-chat',state:'completed',url:'/read/paper'};
  const failed={id:'activity-failed',kind:'activity',activity_kind:'babeldoc',state:'failed'};
  const running={id:'activity-running',kind:'activity',activity_kind:'reader-chat',state:'running'};
  const h=harness(async url=>({task:{...(url.includes('/activities/')?activity:imported),acknowledged:true}}));
  h.context.window.monitorTest.state.tasks=[research];h.PR.importTasks.sync([imported],'scope');h.PR.activityTasks.sync([activity,failed,running],'scope');
  h.PR.researchTaskMonitor.show(h.PR.activityTasks.list()[0]);h.click(h.modal().querySelector('[data-task-ack-all]'));await tick();
  assert.deepEqual(new Set(h.calls.filter(c=>c.method==='POST').map(c=>c.url)),new Set([
    '/api/research/v1/tasks/research-done/ack','/api/import-tasks/'+imported.id+'/ack','/api/research/v1/activities/activity-done/ack']));
  assert.equal(h.PR.researchTaskMonitor.completedCount(),0);
  assert.equal(h.PR.activityTasks.list().find(t=>t.id===failed.id).acknowledged,false);
  assert.equal(h.PR.activityTasks.list().find(t=>t.id===running.id).state,'running');
});

test('monitor confirmation clears a hanging activity acknowledgement and restores usable controls',async()=>{
  const pending=deferred(), activity={id:'lost-ack',kind:'activity',activity_kind:'reader-chat',state:'completed',url:'/read/paper'};
  const h=harness(()=>pending.promise);h.PR.activityTasks.sync([activity],'scope');h.PR.researchTaskMonitor.show(activity);
  const saving=h.PR.activityTasks.ack(h.PR.activityTasks.list()[0]);
  assert.equal(h.modal().querySelector('[data-task-view]').disabled,true);
  h.PR.activityTasks.sync([{...activity,acknowledged:true}],'scope');h.PR.researchTaskMonitor.refresh();
  assert.equal(h.modal().querySelector('[data-task-view]').disabled,false);
  assert.equal(h.modal().querySelector('[data-task-ack]'),null);
  assert.equal([...h.timers.values()].some(t=>t.ms===15000),false);
  pending.resolve({task:{...activity,acknowledged:true}});await saving;
});

test('an unconfirmed activity acknowledgement becomes retryable without replay and ignores stale failures',async()=>{
  const first=deferred(),second=deferred(),activity={id:'retry-ack',kind:'activity',activity_kind:'reader-chat',state:'completed',url:'/read/paper'};
  let writes=0;const h=harness(()=> (++writes===1?first:second).promise);
  h.PR.activityTasks.sync([activity],'scope');h.PR.researchTaskMonitor.show(activity);const record=h.PR.activityTasks.list()[0];
  const one=h.PR.activityTasks.ack(record);h.deadline();
  assert.equal(record.acknowledged,false);assert.equal(h.modal().querySelector('[data-task-ack]').disabled,false);
  assert.match(h.modal().innerHTML,/请求状态待确认/);assert.equal(writes,1);
  const two=h.PR.activityTasks.ack(record);first.reject(Error('old failure'));await one;
  assert.equal(h.modal().querySelector('[data-task-ack]').disabled,true);assert.doesNotMatch(h.modal().innerHTML,/old failure/);
  second.resolve({task:{...activity,acknowledged:true}});await two;
  assert.equal(record.acknowledged,true);assert.equal(h.modal().querySelector('[data-task-view]').disabled,false);
  assert.equal(writes,2);
});

test('hiding the capsule preserves task state, import completion callbacks and explicit details', async()=>{
  const running=task(51,'running'),done={...running,state:'completed'};
  const h=harness(async()=>({scope_id:'scope',tasks:[running]}));
  h.PR.importTasks.sync([running],'scope');h.PR.researchTaskMonitor.refresh();assert.equal(h.entry().hidden,false);
  h.PR.features.task_notifications=false;h.PR.emit('ui-changed');await tick();assert.equal(h.entry().hidden,true);
  h.PR.importTasks.sync([done],'scope');h.PR.researchTaskMonitor.refresh();await tick();
  assert.equal(h.loads.length,1);assert.equal(h.PR.importTasks.list()[0].acknowledged,false);
  h.PR.researchTaskMonitor.show(done);assert.equal(h.modal().hidden,false,'original explicit task details stay usable');
  assert.equal(h.calls.filter(c=>c.method==='POST').length,0);
  h.PR.features.task_notifications=true;h.PR.researchTaskMonitor.refresh();assert.equal(h.entry().hidden,false);
});

test('inactive frames and pagehide pause capsule reads, with one refresh and no overlapping poll on return', async()=>{
  const pending=deferred();const h=harness(url=>url.endsWith('/monitor')?pending.promise:Promise.resolve({task:null}));
  let active=false;h.PR.isPageActive=()=>active;
  await h.PR.researchTaskMonitor.poll();assert.equal(h.calls.length,0);assert.equal(h.timers.size,0);
  active=true;const first=h.PR.researchTaskMonitor.poll();await h.PR.researchTaskMonitor.poll();
  assert.equal(h.calls.filter(c=>c.url.endsWith('/monitor')).length,1);
  h.document.hidden=true;h.listeners.visibilitychange.forEach(fn=>fn());
  pending.resolve({scope_id:'scope',tasks:[{id:'research-finished',kind:'comparison',state:'completed'}]});await first;
  assert.equal(h.timers.size,0);assert.equal(h.entry().hidden,true);
  assert.equal(h.calls.filter(c=>c.method==='POST').length,0,'hidden completions are not silently acknowledged');
  h.document.hidden=false;h.windowEvents.pagehide.forEach(fn=>fn());await h.PR.researchTaskMonitor.poll();
  assert.equal(h.calls.filter(c=>c.url.endsWith('/monitor')).length,1);
  h.windowEvents.pageshow.forEach(fn=>fn());await tick();
  assert.equal(h.calls.filter(c=>c.url.endsWith('/monitor')).length,2);
  assert.equal([...h.timers.values()].filter(timer=>timer.ms===7000).length,1);
});

test('an already-open research dialog cannot acknowledge a completion received while hidden',async()=>{
  const pending=deferred(), running={id:'open-task',kind:'mindmap',state:'running'};
  const h=harness(url=>url.endsWith('/monitor')?pending.promise:Promise.resolve({task:null}));
  h.PR.researchTaskMonitor.track(running,true);const signature=h.modal().dataset.signature;
  const first=h.PR.researchTaskMonitor.poll();h.document.hidden=true;h.listeners.visibilitychange.forEach(fn=>fn());
  pending.resolve({scope_id:'scope',tasks:[{...running,state:'completed'}]});await first;
  assert.equal(h.calls.filter(c=>c.method==='POST').length,0);
  assert.equal(h.modal().dataset.signature,signature,'do not mark an unseen dialog as rendered');
  h.document.hidden=false;h.listeners.visibilitychange.forEach(fn=>fn());await tick();
  assert.match(h.modal().innerHTML,/论文心智图已完成/);
  assert.ok(h.calls.some(c=>c.url.endsWith('/ack')));
});

test('a workspace response arriving after the switch is disabled cannot toast or acknowledge research results',async()=>{
  const pending=deferred();let toasts=0;
  const h=harness(()=>pending.promise,new Map(),{workspace:true});h.PR.toast=()=>toasts++;
  const first=h.PR.researchTaskMonitor.poll();h.PR.features.task_notifications=false;h.PR.emit('ui-changed');
  pending.resolve({scope_id:'scope',tasks:[{id:'late-task',kind:'comparison',state:'completed'}]});await first;
  assert.equal(toasts,0);assert.equal(h.calls.filter(c=>c.method==='POST').length,0);
  assert.equal(h.entry().hidden,true);assert.equal(h.timers.size,0);
});

test('eviction of an open activity detail closes it without inventing a failed research task',()=>{
  const h=harness(async()=>({})), activity={id:'evicted',kind:'activity',activity_kind:'reader-chat',state:'completed'};
  h.PR.activityTasks.sync([activity],'scope');h.PR.researchTaskMonitor.show(activity);
  assert.equal(h.modal().hidden,false);
  h.PR.activityTasks.sync([],'scope');h.PR.researchTaskMonitor.refresh();assert.equal(h.modal().hidden,true);
  assert.equal(h.calls.length,0);
});

test('workspace notification polling is lightweight and stops when its capsule is disabled',async()=>{
  const h=harness(async()=>({scope_id:'scope',token:'new',tasks:[],activities:[]}),new Map(),{workspace:true,enabled:false});
  await h.PR.researchTaskMonitor.poll();assert.equal(h.calls.length,0);
  h.PR.features.task_notifications=true;h.PR.emit('ui-changed');await tick();
  assert.deepEqual(h.calls.map(c=>c.url),['/api/research/v1/monitor']);
  h.PR.features.task_notifications=false;h.PR.emit('ui-changed');assert.equal(h.timers.size,0);
});

test('all monitor hosts include the common import adapter; tabs use those hosts and offline exports remain inert', () => {
  for (const page of ['library', 'reader', 'research', 'workspaces']) {
    const html = read(page + '.html');
    assert.ok(html.indexOf('/common/import-tasks.js') >= 0, page);
    assert.ok(html.indexOf('/common/import-tasks.js') < html.indexOf('/common/research-task-monitor.js'), page);
  }
  assert.doesNotMatch(read('tabs.html'), /research-task-monitor/);
  assert.match(read('js/tabs.js'), /iframe/);
  for (const file of ['import-tasks', 'research-task-monitor', 'activity-tasks', 'task-monitor-runtime']) {
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

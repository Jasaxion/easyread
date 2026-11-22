const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function fixture() {
  const pending = [], calls = [], timers = new Map(), events = {}, nodes = new Map();
  let timerId = 0;
  const mount = { replacements: 0, replaceChildren(frame) { this.frame = frame; frame.parentElement = this; this.replacements++; } };
  const state = { tab: 'terminal', cfg: { embedded_terminal: true } };
  const target = extra => Object.assign({ listeners: {}, addEventListener(name, fn) { this.listeners[name] = fn; },
    removeEventListener(name) { delete this.listeners[name]; } }, extra);
  const node = s => { if (!nodes.has(s)) nodes.set(s, target({})); return nodes.get(s); };
  const root = { classList: { contains: () => state.tab !== 'closed' }, querySelector: s => s === '#terminalMount' ? mount : node(s) };
  const PR = { settingsTabs: {}, t: x => x, esc: x => x, toast() {}, confirm: async () => true, settingsRender() {},
    api(url, options) { calls.push({url,body:options?.body}); if (url.endsWith('/open')) return new Promise(resolve => pending.push(resolve)); return Promise.resolve(url.endsWith('/status') ? {items:[{id:'pi',installed:false}]} : {}); },
  };
  const document = { hidden: false, documentElement: { dataset: { theme: 'light' } }, addEventListener: (name, fn) => { events[name] = fn; },
    createElement: () => {
      const frame = target({ src: '', setAttribute() {}, getAttribute() { return this.src; }, remove() { this.removed = true; } });
      frame.contentWindow = { location: { get href() { return 'http://localhost' + frame.src; } }, term: { options: {} } };
      return frame;
    } };
  const storage = new Map();
  const context = vm.createContext({ window: { PR, addEventListener: (name, fn) => { events[name] = fn; }, removeEventListener: name => { delete events[name]; } }, document,
    URL, location: { href: 'http://localhost/', origin: 'http://localhost' },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
    MutationObserver: class { observe() {} disconnect() {} },
    setTimeout: (fn, delay) => { timers.set(++timerId, {fn, delay}); return timerId; }, clearTimeout: id => timers.delete(id) });
  for (const file of ['settings-models.js', 'terminal-theme.js', 'settings-terminal.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../../easyread/web/js/common/' + file), 'utf8'), context);
  }
  const button = { dataset: { cliAction: 'shell' }, disabled: false };
  return { PR, state, root, pending, calls, mount, button, document, events, timers, node,
    fire: async () => { const [id, timer] = timers.entries().next().value; timers.delete(id); await timer.fn(); },
    refresh: () => PR.settingsTabs.terminal.click({ target: { closest: s => s === '#refreshCliStatus' ? {} : null } }, state, root),
    click: () => PR.settingsTabs.terminal.click({ target: { closest: s => s === '[data-cli-action]' ? button : null } }, state, root) };

}

test('terminal startup is single-flight before the first await', async () => {
  const f = fixture(), first = f.click();
  await f.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.pending.length, 1);
  f.pending[0]({url: '/terminal/one/'}); await first;
  assert.equal(f.mount.frame.src, '/terminal/one/');
});

test('stale open completion cleans only its own terminal, not the replacement', async () => {
  const f = fixture(), first = f.click();
  await new Promise(resolve => setImmediate(resolve));
  await f.PR.settingsTabs.terminal.dispose();
  const second = f.click();
  await new Promise(resolve => setImmediate(resolve));
  f.pending[1]({url: '/terminal/new/'}); await second;
  f.pending[0]({url: '/terminal/old/'}); await first;
  assert.equal(f.mount.frame.src, '/terminal/new/');
  const closes = f.calls.filter(x => x.url.endsWith('/close'));
  assert.equal(closes.at(-1).body.url, '/terminal/old/');
  await f.PR.settingsTabs.terminal.dispose();
  assert.equal(f.calls.at(-1).body.url, '/terminal/new/');
  assert.equal(f.PR.settingsTabs.terminal.active(), false);
});

test('Pi card reports actual installation, exact npm command and native login/status instructions', () => {
  const f = fixture(), tab = f.PR.settingsTabs.terminal;
  f.state.cliStatus = { items: [{ id: 'claude', installed: true, version: 'Claude version' }, { id: 'pi', installed: false }] };
  let html = tab.render(f.state);
  assert.match(html, /Pi Agent/); assert.match(html, /data-cli-action="pi-install"/);
  assert.match(html, /data-cli-action="pi-login" disabled/); assert.match(html, /未安装/);
  assert.match(html, /npm install -g --ignore-scripts @earendil-works\/pi-coding-agent@latest/);
  assert.match(html, /22\.19/); assert.match(html, /\/login/); assert.match(html, /版本号不代表已登录/);
  assert.doesNotMatch(html, /pi update|--credentials/);
  f.state.cliStatus.items[1] = { id: 'pi', installed: true, version: '1.6.0' };
  html = tab.render(f.state);
  assert.match(html, /data-cli-action="pi-update"/); assert.match(html, /data-cli-action="pi-status"/);
  assert.doesNotMatch(html, /data-cli-action="pi-login" disabled|data-cli-action="pi-install"/);
});

test('Pi terminal management delegates each action without inventing CLI arguments', async () => {
  for (const action of ['pi-install', 'pi-update', 'pi-login', 'pi-status']) {
    const f = fixture(); f.button.dataset.cliAction = action;
    const opening = f.click(); await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(JSON.parse(JSON.stringify(f.calls[0])), { url: '/api/terminal/open', body: { action } });
    f.pending[0]({ url: '/terminal/pi/' }); await opening;
    assert.equal(f.mount.frame.src, '/terminal/pi/');
  }
});

test('disconnect refreshes installation status so a completed Pi install can expose login', async () => {
  const f=fixture(),tab=f.PR.settingsTabs.terminal;
  f.state.cliStatus={items:[{id:'pi',installed:false}]};
  assert.equal(await tab.click({target:{closest:selector=>selector==='#closeTerminal'?{}:null}},f.state,f.root),true);
  assert.equal(f.state.cliStatus,null);
  f.PR.api=async url=>{assert.equal(url,'/api/terminal/status');return {items:[{id:'pi',installed:true,version:'new'}]};};
  await tab.mount(f.state,f.root);
  assert.doesNotMatch(tab.render(f.state),/data-cli-action="pi-login" disabled/);
});

const flush = () => new Promise(resolve => setImmediate(resolve));
const installed = version => ({items:[{id:'pi',installed:true,version}]});

test('Pi installation is detected while its terminal stays mounted and no action is replayed', async () => {
  const f = fixture(), tab = f.PR.settingsTabs.terminal;
  f.state.cliStatus = {items:[{id:'pi',installed:false}]};
  await tab.mount(f.state, f.root);
  f.button.dataset.cliAction = 'pi-install';
  const opening = f.click(); await flush(); f.pending[0]({url:'/terminal/install/'}); await opening; await flush();
  const frame = f.mount.frame, before = f.mount.replacements;
  const api = f.PR.api;
  f.PR.api = (url, options) => url.endsWith('/status') ? Promise.resolve(installed('1.6.0')) : api(url, options);
  assert.equal(f.timers.values().next().value.delay, 15000);
  await f.fire();
  assert.equal(f.state.cliStatus.items[0].version, '1.6.0');
  assert.match(f.node('.cli-cards').innerHTML, /1\.6\.0/);
  assert.doesNotMatch(f.node('.cli-cards').innerHTML, /data-cli-action="pi-login" disabled|data-cli-action="pi-install"/);
  assert.equal(f.mount.frame, frame); assert.equal(frame.src, '/terminal/install/'); assert.equal(frame.removed, undefined);
  assert.equal(f.mount.replacements, before);
  assert.equal(f.calls.filter(c => c.url.endsWith('/open')).length, 1);
  assert.equal(f.calls.filter(c => c.url.endsWith('/close')).length, 0);
  await tab.mount(f.state, f.root);
  assert.equal(f.mount.replacements, before, 'mount itself must not reinsert an existing iframe');
  assert.equal(await f.refresh(), false, 'refresh must not trigger the outer settings renderer');
});

test('status reads are single-flight and stale results cannot overwrite a reopened settings panel', async () => {
  const f = fixture(), tab = f.PR.settingsTabs.terminal, pending = [];
  let calls = 0;
  f.PR.api = url => { assert.equal(url, '/api/terminal/status'); calls++; return new Promise(resolve => pending.push(resolve)); };
  const first = tab.mount(f.state, f.root); await flush();
  const refresh = f.refresh(); f.events.focus();
  assert.equal(calls, 1);
  await tab.dispose(); f.state.cliStatus = null;
  const next = tab.mount(f.state, f.root);
  pending[0](installed('obsolete')); await first; await refresh; await next;
  assert.equal(f.state.cliStatus, null);
  assert.equal(f.timers.values().next().value.delay, 0);
  const latest = f.fire(); await flush(); assert.equal(calls, 2);
  pending[1](installed('current')); await latest;
  assert.equal(f.state.cliStatus.items[0].version, 'current');
});

test('status polling pauses in hidden or inactive pages, resumes on focus, and stops on disposal', async () => {
  const f = fixture(), tab = f.PR.settingsTabs.terminal;
  await tab.mount(f.state, f.root);
  const before = f.calls.length;
  f.document.hidden = true; f.events.visibilitychange(); assert.equal(f.timers.size, 0);
  await f.refresh(); assert.equal(f.calls.length, before);
  f.document.hidden = false; f.events.visibilitychange(); await flush();
  assert.equal(f.calls.length, before + 1); assert.equal(f.timers.size, 1);
  f.PR.isPageActive = () => false; f.events['easyread:visibility'](); assert.equal(f.timers.size, 0);
  f.PR.isPageActive = () => true; f.events['easyread:visibility'](); await flush(); assert.equal(f.timers.size, 1);
  f.events.pagehide(); assert.equal(f.timers.size, 0);
  f.events.pageshow(); await flush(); assert.equal(f.timers.size, 1);
  await tab.dispose(); const closed = f.calls.length;
  f.events.focus(); await flush(); assert.equal(f.calls.length, closed); assert.equal(f.timers.size, 0);
});

test('failed/incomplete status reads retain prior installation instead of falsely reporting missing', async () => {
  const f = fixture(), tab = f.PR.settingsTabs.terminal;
  f.PR.api = async () => installed('1.6.0'); await tab.mount(f.state, f.root);
  f.PR.api = () => { throw new Error('temporarily unavailable'); };
  await f.refresh();
  assert.equal(f.state.cliStatus.items[0].installed, true);
  assert.match(f.node('#cliScanStatus').textContent, /保留上次结果/);
  assert.match(f.node('.cli-cards').innerHTML, /1\.6\.0/);
  f.PR.api = async () => ({}); await f.refresh();
  assert.match(f.state.cliStatusError, /响应不完整/);
  f.PR.api = async () => installed('1.6.1'); await f.refresh();
  assert.equal(f.state.cliStatus.items[0].version, '1.6.1', 'a synchronous API error must not strand the single-flight promise');
  assert.equal(f.state.cliStatusError, '');
});

test('installed CLI with failed version probe is not shown as missing or perpetually detecting', () => {
  const f = fixture(), tab = f.PR.settingsTabs.terminal;
  for (const extra of [{version_error:'timeout'}, {version_error:'command_failed'}, {version:''}]) {
    f.state.cliStatus = {items:[{id:'pi',installed:true,...extra}]};
    const html = tab.render(f.state);
    assert.match(html, /已安装/);
    assert.doesNotMatch(html, /data-cli-action="pi-install"|data-cli-action="pi-login" disabled/);
  }
});

test('settings-terminal: shared theme control changes the existing runtime without reopening or remounting', async () => {
  const f = fixture(), tab = f.PR.settingsTabs.terminal;
  await tab.mount(f.state, f.root);
  const opening = f.click(); await flush(); f.pending[0]({ url: '/terminal/theme/' }); await opening;
  const frame = f.mount.frame, replacements = f.mount.replacements, calls = f.calls.length;
  const control = f.node('#settings-terminal-theme');
  assert.equal(frame.contentWindow.term.options.theme.background, '#fffcf0');
  control.value = 'dark'; control.listeners.change();
  assert.equal(frame.contentWindow.term.options.theme.background, '#100f0f');
  assert.equal(f.mount.frame, frame); assert.equal(f.mount.replacements, replacements); assert.equal(f.calls.length, calls);
  assert.equal(frame.src, '/terminal/theme/'); assert.equal(frame.removed, undefined);
  await tab.dispose();
  assert.equal(frame.listeners.load, undefined); assert.equal(control.listeners.change, undefined);
});

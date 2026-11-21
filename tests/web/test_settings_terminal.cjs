const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function fixture() {
  const pending = [], calls = [], mount = { replaceChildren(frame) { this.frame = frame; } };
  const state = { tab: 'terminal', cfg: { embedded_terminal: true } };
  const root = { classList: { contains: () => state.tab !== 'closed' }, querySelector: s => s === '#terminalMount' ? mount : {} };
  const PR = { settingsTabs: {}, t: x => x, esc: x => x, toast() {}, confirm: async () => true, settingsRender() {},
    api(url, options) { calls.push({url,body:options?.body}); if (url.endsWith('/open')) return new Promise(resolve => pending.push(resolve)); return Promise.resolve({}); },
  };
  const document = { createElement: () => ({ src: '', setAttribute() {}, getAttribute() { return this.src; }, remove() {} }) };
  for (const file of ['settings-models.js', 'settings-terminal.js']) {
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../easyread/web/js/common/' + file), 'utf8'), { window: { PR }, document });
  }
  const button = { dataset: { cliAction: 'shell' }, disabled: false };
  return { PR, state, root, pending, calls, mount, button, click: () => PR.settingsTabs.terminal.click({ target: { closest: s => s === '[data-cli-action]' ? button : null } }, state, root) };
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

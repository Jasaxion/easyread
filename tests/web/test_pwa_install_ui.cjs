const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../../easyread/web/js/common/pwa-ui.js'), 'utf8');

function fixture(appWindow = false) {
  const nodes = new Map(), events = {}, toasts = [];
  const el = (tag, attrs = {}) => {
    const node = {...attrs, textContent: attrs.text || '', tag, hidden: false, disabled: false, children: [],
      append(...children) { this.children.push(...children); },
      showModal() { this.open = true; }, close() { this.open = false; }};
    if (attrs.id) nodes.set(attrs.id, node);
    return node;
  };
  const PR = {el, toast: text => toasts.push(text),
    t: (text, args = {}) => text.replace(/\{(\w+)\}/g, (_, key) => args[key] ?? `{${key}}`)};
  const context = {location: {protocol: 'https:', origin: 'https://example.test'},
    navigator: {userAgent: 'Android Chrome', platform: 'Linux'}, matchMedia: () => ({matches: appWindow}),
    document: {getElementById: id => nodes.get(id), body: el('body'), addEventListener() {}},
    window: {PR, isSecureContext: true, addEventListener: (type, fn) => { events[type] = fn; }}};
  vm.runInNewContext(source, context);
  PR.openPwaSettings();
  return {PR, nodes, events, toasts, button: nodes.get('pwaInstall'), status: nodes.get('pwaInstallStatus')};
}

test('installation entry stays device-local and visible after a consumed prompt', async () => {
  const a = fixture(), b = fixture();
  let calls = 0;
  a.events.beforeinstallprompt({preventDefault() {}, prompt: async () => { calls++; }, userChoice: Promise.resolve({outcome: 'dismissed'})});
  assert.equal(a.status.textContent, '当前浏览器可显示安装弹窗。');
  assert.match(b.status.textContent, /暂未提供安装弹窗/);
  await a.button.onclick();
  await a.button.onclick();
  await b.button.onclick();
  assert.equal(calls, 1, 'a native prompt is consumed only once');
  for (const page of [a, b]) {
    assert.equal(page.button.textContent, '安装到此设备');
    assert.equal(page.button.hidden, false);
    assert.match(page.toasts[0], /暂未提供安装弹窗/);
  }
});

test('fullscreen/standalone appearance and appinstalled do not hide the entry', () => {
  const page = fixture(true);
  page.events.appinstalled();
  assert.equal(page.button.hidden, false);
  assert.equal(page.button.textContent, '安装到此设备');
  assert.match(page.status.textContent, /暂未提供安装弹窗/);
});

test('duplicate clicks and a failed native prompt leave a usable installation entry', async () => {
  const page = fixture();
  let reject, calls = 0;
  page.events.beforeinstallprompt({preventDefault() {}, prompt: () => { calls++; return new Promise((_, fail) => { reject = fail; }); }});
  const pending = page.button.onclick();
  assert.equal(page.button.disabled, true);
  await page.button.onclick();
  reject(new Error('browser refused'));
  await pending;
  assert.equal(calls, 1);
  assert.equal(page.button.disabled, false);
  assert.equal(page.button.hidden, false);
  assert.match(page.toasts[0], /无法打开安装弹窗/);
});

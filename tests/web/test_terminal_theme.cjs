const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../easyread/web/js/common/terminal-theme.js'), 'utf8');
const KEY = 'easyread-terminal-theme';
const light = {
  foreground: '#100f0f', background: '#fffcf0', cursor: '#100f0f', cursorAccent: '#fffcf0',
  selectionBackground: '#e6e4d9', selectionForeground: '#100f0f',
  black: '#100f0f', red: '#af3029', green: '#66800b', yellow: '#ad8301',
  blue: '#205ea6', magenta: '#a02f6f', cyan: '#24837b', white: '#6f6e69',
  brightBlack: '#b7b5ac', brightRed: '#d14d41', brightGreen: '#879a39', brightYellow: '#d0a215',
  brightBlue: '#4385be', brightMagenta: '#ce5d97', brightCyan: '#3aa99f', brightWhite: '#cecdc3',
};
const plain = value => JSON.parse(JSON.stringify(value));
function target(extra = {}) {
  const listeners = new Map();
  return Object.assign({ listeners,
    addEventListener(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); },
    dispatch(name, event = {}) { for (const fn of listeners.get(name) || []) fn(event); },
    count(name) { return listeners.get(name)?.size || 0; },
  }, extra);
}
function fixture(stored = null, theme = 'light') {
  const values = new Map(stored === null ? [] : [[KEY, stored]]), timers = new Map(), observers = [];
  let next = 0;
  const localStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  const document = { documentElement: { dataset: { theme } } }, PR = { t: value => value, esc: value => value };
  const window = target({ PR });
  const location = { href: 'http://localhost:8765/workspaces', origin: 'http://localhost:8765' };
  class MutationObserver {
    constructor(fn) { this.fn = fn; observers.push(this); }
    observe(node, options) { this.node = node; this.options = options; this.active = true; }
    disconnect() { this.active = false; }
  }
  vm.runInNewContext(source, { window, document, location, localStorage, URL, MutationObserver,
    setTimeout: (fn, delay) => { const id = ++next; timers.set(id, { fn, delay }); return id; }, clearTimeout: id => timers.delete(id) });
  function frame(src = '/workspace-terminal/session_1/', ready = true) {
    let currentTheme;
    const changes = [], options = { fontSize: 14, cursorBlink: true };
    Object.defineProperty(options, 'theme', { get: () => currentTheme, set: value => { changes.push(value); currentTheme = value; } });
    const forbidden = () => assert.fail('theme changes must not call terminal/iframe lifecycle, input, resize, or focus methods');
    const term = { options, buffer: { active: { baseY: 123, viewportY: 48 } }, draft: 'unsent command', process: { pid: 123 },
      write: forbidden, reset: forbidden, clear: forbidden, dispose: forbidden, focus: forbidden, fit: forbidden, resize: forbidden };
    const runtime = { location: { href: new URL(src, location.href).href }, term: ready ? term : null };
    const result = target({ src, contentWindow: runtime, getAttribute(name) { return this[name]; },
      setAttribute: forbidden, remove: forbidden, replaceWith: forbidden, focus: forbidden,
      reload: forbidden, term, changes });
    Object.defineProperty(result, 'src', { get: () => src, set: forbidden });
    return result;
  }
  return { T: PR.terminalTheme, frame, values, timers, observers, localStorage, window, document,
    changeTheme(theme) { document.documentElement.dataset.theme = theme; for (const observer of observers) if (observer.active) observer.fn(); },
    tick() { const entry = timers.entries().next().value; if (!entry) return false; const [id, timer] = entry; timers.delete(id); timer.fn(); return true; },
  };
}

test('terminal-theme: exact modern-light palette and live updates preserve iframe, scrollback, input and process identity', () => {
  const f = fixture(), frame = f.frame(), original = { src: frame.src, runtime: frame.contentWindow, term: frame.term,
    buffer: frame.term.buffer, process: frame.term.process, options: frame.term.options };
  f.T.register(frame);
  assert.deepEqual(plain(frame.term.options.theme), light);
  f.T.setSelection('dark');
  const dark = plain(frame.term.options.theme);
  assert.equal(dark.background, '#100f0f'); assert.equal(dark.foreground, '#cecdc3'); assert.equal(dark.cursor, '#cecdc3');
  assert.deepEqual(Object.keys(dark).sort(), Object.keys(light).sort());
  for (const value of Object.values(dark)) assert.match(value, /^#[0-9a-f]{6}$/);
  f.T.setSelection('modern-light'); assert.deepEqual(plain(frame.term.options.theme), light);
  assert.equal(frame.src, original.src); assert.equal(frame.contentWindow, original.runtime); assert.equal(frame.term, original.term);
  assert.equal(frame.term.buffer, original.buffer); assert.equal(frame.term.buffer.active.viewportY, 48);
  assert.equal(frame.term.process, original.process); assert.equal(frame.term.draft, 'unsent command');
  assert.equal(frame.term.options, original.options); assert.equal(frame.term.options.fontSize, 14); assert.equal(frame.term.options.cursorBlink, true);
  assert.equal(frame.changes.length, 3); assert.equal(f.timers.size, 0);
});

test('terminal-theme: auto follows data-theme while fixed choices ignore application theme changes', () => {
  const f = fixture(), frame = f.frame(); f.T.register(frame);
  assert.equal(f.T.getSelection(), 'auto');
  assert.deepEqual(plain(f.observers[0].options), { attributes: true, attributeFilter: ['data-theme'] });
  f.changeTheme('dark'); assert.equal(frame.term.options.theme.background, '#100f0f');
  f.changeTheme('light'); assert.equal(frame.term.options.theme.background, '#fffcf0');
  f.T.setSelection('modern-light'); const count = frame.changes.length;
  f.changeTheme('dark'); assert.equal(frame.term.options.theme.background, '#fffcf0'); assert.equal(frame.changes.length, count);
  f.T.setSelection('dark'); f.changeTheme('light'); assert.equal(frame.term.options.theme.background, '#100f0f');
  f.T.setSelection('auto'); assert.equal(frame.term.options.theme.background, '#fffcf0');
});

test('terminal-theme: stored choice, shared controls, cross-page storage and inaccessible storage', () => {
  const f = fixture('dark'), first = target({ value: '' }), second = target({ value: '' });
  const releaseFirst = f.T.bindControl(first), releaseSecond = f.T.bindControl(second);
  const local = f.frame('/terminal/local/'), remote = f.frame('/workspace-terminal/ssh/');
  f.T.register(local); f.T.register(remote);
  assert.equal(first.value, 'dark'); assert.equal(second.value, 'dark');
  first.value = 'modern-light'; first.dispatch('change');
  assert.equal(f.values.get(KEY), 'modern-light'); assert.equal(second.value, 'modern-light');
  assert.deepEqual(plain(local.term.options.theme), light); assert.deepEqual(plain(remote.term.options.theme), light);
  f.values.set(KEY, 'dark'); f.window.dispatch('storage', { key: KEY, storageArea: f.localStorage });
  assert.equal(first.value, 'dark'); assert.equal(second.value, 'dark'); assert.equal(local.term.options.theme.background, '#100f0f');
  f.values.clear(); f.window.dispatch('storage', { key: null, storageArea: f.localStorage });
  assert.equal(first.value, 'auto');
  f.T.setSelection('not-a-theme'); assert.equal(f.T.getSelection(), 'auto');
  f.localStorage.setItem = () => { throw new Error('storage blocked'); };
  assert.doesNotThrow(() => f.T.setSelection('dark')); assert.equal(first.value, 'dark');
  releaseFirst(); releaseSecond(); assert.equal(first.count('change'), 0); assert.equal(second.count('change'), 0);
  assert.equal(fixture('bad-stored-value').T.getSelection(), 'auto');
  const blocked = fixture();
  blocked.localStorage.setItem = () => { throw new Error('quota exceeded'); };
  blocked.T.setSelection('dark'); const later = blocked.frame(); blocked.T.register(later);
  assert.equal(later.term.options.theme.background, '#100f0f', 'an unsaved choice still applies to terminals opened later on this page');
});

test('terminal-theme: async ttyd initialization uses latest preference, load retries, and bounded unsupported-runtime wait', () => {
  const f = fixture(), frame = f.frame('/terminal/delayed/', false);
  const release = f.T.register(frame); assert.equal(f.timers.size, 1); assert.equal(f.timers.values().next().value.delay, 100);
  f.T.setSelection('dark'); f.tick(); assert.equal(frame.changes.length, 0);
  frame.contentWindow.term = frame.term; f.tick();
  assert.equal(frame.term.options.theme.background, '#100f0f'); assert.equal(f.timers.size, 0);
  frame.contentWindow.term = null; frame.dispatch('load');
  let attempts = 0; while (f.tick()) { assert.ok(++attempts <= 100, 'startup polling must stop'); }
  assert.equal(attempts, 99); assert.equal(f.timers.size, 0);
  frame.contentWindow.term = frame.term; frame.dispatch('load');
  assert.equal(frame.changes.length, 2, 'a genuine later load can initialize again without module navigation');
  release(); assert.equal(frame.count('load'), 0);
  for (const term of [{}, { options: Object.defineProperty({}, 'theme', { set() { throw new Error('unsupported runtime'); } }) }]) {
    const unsupported = f.frame(); unsupported.contentWindow.term = term;
    const cleanup = f.T.register(unsupported);
    assert.doesNotThrow(() => f.T.setSelection('modern-light')); cleanup();
  }
  assert.equal(f.timers.size, 0);
});

test('terminal-theme: unregister cancels timers/listeners and drops disposed frames, without restoring or resetting the terminal', () => {
  const f = fixture(), frame = f.frame(), delayed = f.frame('/terminal/waiting/', false);
  const dispose = f.T.register(frame), cancel = f.T.register(delayed);
  assert.equal(f.T.register(frame), dispose, 'duplicate registration does not add listeners'); assert.equal(frame.count('load'), 1);
  const count = frame.changes.length; dispose(); dispose();
  f.T.setSelection('dark'); f.changeTheme('dark'); assert.equal(frame.changes.length, count);
  cancel(); assert.equal(f.timers.size, 0); assert.equal(delayed.count('load'), 0); assert.equal(f.window.count('storage'), 0);
  assert.ok(f.observers.every(observer => !observer.active));
  delayed.contentWindow.term = delayed.term; delayed.dispatch('load'); assert.equal(delayed.changes.length, 0);
  const select = target({ value: '' }), oldBinding = f.T.bindControl(select);
  oldBinding(); const newBinding = f.T.bindControl(select); oldBinding();
  assert.equal(select.count('change'), 1, 'an old cleanup cannot unregister a new binding');
  newBinding(); assert.equal(select.count('change'), 0); assert.equal(f.window.count('storage'), 0);
});

test('terminal-theme: only explicitly owned same-origin terminal namespaces can be changed, including after redirects', () => {
  const f = fixture();
  const unowned = f.frame(); f.T.setSelection('dark'); assert.equal(unowned.changes.length, 0);
  for (const src of ['https://elsewhere.test/terminal/a/', 'http://localhost:9999/terminal/a/', '/web/terminal/a/', '/terminal/',
    '/workspace-terminal/a/more', '/terminal/a/?q=1', '/terminal/a/#hash', 'http://user@localhost:8765/terminal/a/', 'about:blank', 'javascript:alert(1)']) {
    const frame = f.frame(src);
    Object.defineProperty(frame, 'contentWindow', { get() { assert.fail('disallowed src must not be accessed'); } });
    const release = f.T.register(frame); assert.equal(frame.count('load'), 0); assert.equal(f.timers.size, 0); release();
  }
  for (const destination of ['https://elsewhere.test/terminal/a/', 'http://localhost:8765/read/paper', 'about:blank']) {
    const frame = f.frame('/terminal/a/'); frame.contentWindow.location.href = destination;
    const release = f.T.register(frame); f.T.setSelection('modern-light'); assert.equal(frame.changes.length, 0); release();
  }
  const inaccessible = f.frame();
  Object.defineProperty(inaccessible, 'contentWindow', { get() { throw new Error('SecurityError'); } });
  let release; assert.doesNotThrow(() => { release = f.T.register(inaccessible); f.T.setSelection('dark'); }); release();
  assert.equal(f.timers.size, 0);
});

test('terminal-theme: both surfaces load the shared module and controls are labelled with the Agent-theme limitation', () => {
  const f = fixture(), html = f.T.controlHtml('test-theme');
  assert.match(html, /for="test-theme"/); assert.match(html, /id="test-theme" aria-describedby="test-theme-hint"/);
  assert.match(html, /value="auto" selected/); assert.match(html, /value="dark"/); assert.match(html, /value="modern-light"/);
  assert.match(html, /Pi.*真彩色.*应用内设置/);
  for (const name of ['library', 'reader', 'workspaces']) {
    const source = fs.readFileSync(path.join(__dirname, `../../easyread/web/${name}.html`), 'utf8');
    const shared = source.indexOf('/js/common/terminal-theme.js');
    assert.ok(shared >= 0);
    assert.ok(shared < source.indexOf(name === 'workspaces' ? '/js/workspaces/session-terminal.js' : '/js/common/settings-terminal.js'));
  }
});

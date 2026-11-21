const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const scripts = path.resolve(__dirname, '../../easyread/web/js');

function shell() {
  const window = {};
  vm.runInNewContext(fs.readFileSync(path.join(scripts, 'common/tabs-visibility.js'), 'utf8'), { window, WeakMap });
  return window.PRTabsVisibility;
}
function frame() {
  const events = [], PR = { workspaces: { draft: 'unsaved', session: { state: 'running' } } };
  const doc = { hidden: false, dispatchEvent: event => events.push(event) };
  const win = { PR, document: doc, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } } };
  return { contentDocument: doc, contentWindow: win, events };
}

test('inactive iframes expose explicit visibility without touching drafts or native work', () => {
  const visibility = shell(), f = frame(), W = f.contentWindow.PR.workspaces;
  visibility.setActive(f, false);
  assert.equal(f.contentDocument.hidden, false, 'CSS hiding does not change browser visibility');
  assert.equal(f.contentWindow.PR.isPageActive(), false);
  assert.equal(f.events.length, 1); assert.equal(f.events[0].type, 'easyread:visibility');
  visibility.setActive(f, false); assert.equal(f.events.length, 1, 'no duplicate wake events');
  visibility.setActive(f, true); assert.equal(f.events.length, 2);
  assert.equal(f.contentWindow.PR.isPageActive(), true);
  assert.equal(W.draft, 'unsaved'); assert.equal(W.session.state, 'running');
});

test('reloaded documents rebind and separate frames do not share visibility state', () => {
  const visibility = shell(), a = frame(), b = frame();
  visibility.setActive(a, true); visibility.setActive(b, false);
  assert.equal(a.contentWindow.PR.isPageActive(), true); assert.equal(b.contentWindow.PR.isPageActive(), false);
  const previous = a.contentWindow, replacement = frame();
  a.contentWindow = replacement.contentWindow; a.contentDocument = replacement.contentDocument;
  visibility.setActive(a, false);
  assert.equal(a.contentWindow.PR.isPageActive(), false);
  assert.equal(previous.PR.isPageActive(), true, 'a navigated-away document is not the new document');
  assert.equal(replacement.events.length, 1);
});

test('unloaded or cross-origin frames are ignored without affecting the shell', () => {
  const visibility = shell();
  assert.doesNotThrow(() => visibility.setActive({}, false));
  assert.doesNotThrow(() => visibility.setActive({ get contentWindow() { throw new Error('cross-origin'); } }, false));
});

test('workspace visibility composes browser and shell visibility, standalone stays supported', () => {
  const PR = {}, document = { hidden: false };
  vm.runInNewContext(fs.readFileSync(path.join(scripts, 'workspaces/core.js'), 'utf8'), { window: { PR }, document });
  assert.equal(PR.workspaces.isVisible(), true);
  PR.isPageActive = () => false; assert.equal(PR.workspaces.isVisible(), false);
  PR.isPageActive = () => true; assert.equal(PR.workspaces.isVisible(), true);
  document.hidden = true; assert.equal(PR.workspaces.isVisible(), false);
});

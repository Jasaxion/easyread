const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const read = file => fs.readFileSync('easyread/web/js/' + file, 'utf8');

test('inserting without a whiteboard only reads and shows the themed reminder', async () => {
  const requests = [], toasts = []; let opened = 0;
  const PR = { pid: 'paper', state: { library_id: 'library', paper: { meta: {} }, reader: {} },
    blockById: { p: { id: 'p', type: 'para' } }, store: { mode: 'server' },
    blockKeys: () => ['p'], textFor: () => 'Paragraph', readingView: () => 'translation',
    feature: () => true, plain: String, t: text => text, uid: () => 'id',
    toast: (...args) => toasts.push(args), openSide: () => { opened++; },
  };
  vm.runInNewContext(read('reader/whiteboard.js'), {
    window: { PR, addEventListener() {} },
    document: { documentElement: { dataset: {} }, addEventListener() {},
      createElement() { throw Error('Must not mount an editor'); } },
    MutationObserver: class { observe() {} }, AbortController, setTimeout, clearTimeout,
    EasyReadWhiteboardDrafts: {},
    fetch: async (_, options) => { requests.push(options.method || 'GET');
      return { ok: true, json: async () => ({ board: null, library_id: 'library' }) }; },
  });
  await PR.whiteboard.insertBlock('p');
  assert.deepEqual(requests, ['GET']); assert.equal(opened, 0);
  assert.equal(toasts[0][0], '当前文档没有白板，请先在侧栏创建白板！');
  assert.equal(toasts[0][1].label, '打开白板');
});

test('paragraph insertion lives only in More, displays V, and shares the keyboard action', () => {
  const calls = [], bar = { innerHTML: '', style: {}, classList: { add() {}, remove() {}, contains: () => true }, offsetWidth: 320, offsetHeight: 32 };
  const block = { classList: { add() {}, remove() {} }, getBoundingClientRect: () => ({ top: 200, right: 600, bottom: 250 }) };
  const PR = { state: { paper: { meta: {} } }, store: { mode: 'server' }, prefs: {}, blockById: { p: { id: 'p' } },
    blockKeys: () => [], hasZh: () => false, canChat: () => false, canAsk: () => false, editOf: () => null, readingBlock: () => 'p',
    t: text => text, esc: String, icon: () => '', throttle: fn => fn, on() {}, ls: { get: (_, fallback) => fallback, set() {} },
    $: selector => selector === '#blockbar' ? bar : { checked: false }, $$: () => [],
    menu: (_, items) => { calls.push(items); }, whiteboard: { insertBlock: id => calls.push(id) },
  };
  const context = { window: { PR, addEventListener() {} }, document: { documentElement: { classList: { add() {} } }, getElementById: () => block, addEventListener() {} }, innerWidth: 1200, innerHeight: 800 };
  for (const file of ['common/features.js', 'reader/actions.js', 'reader/keys.js']) vm.runInNewContext(read(file), context);
  PR.setCurrent('p'); assert.doesNotMatch(bar.innerHTML, /插入到白板/); assert.match(bar.innerHTML, /data-i="more"/);
  PR.showBlockMenu('p', {}); const action = calls[0].find(item => item.label === '插入到白板');
  assert.equal(action.kbd, 'V'); action.fn(); assert.equal(calls.at(-1), 'p');
  assert.equal(PR.keyAction('v'), 'whiteboardInsert'); PR.runAction('whiteboardInsert'); assert.equal(calls.at(-1), 'p');
  PR.applyUi({ features: { whiteboard: false } }); assert.equal(PR.keyAction('v'), null);
  PR.showBlockMenu('p', {}); assert.ok(!calls.at(-1).some(item => item.label === '插入到白板'));
});

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = name => fs.readFileSync(path.join(__dirname, '../../easyread/web/js', name), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const image = { name: 'clip.png', type: 'image/png', size: 24, data: 'data:image/png;base64,aW1hZ2U=' };
const settle = () => new Promise(resolve => setImmediate(resolve));
function harness() {
  const nodes = new Map(), timers = [], reads = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, { id, value: '', disabled: false, listeners: {}, innerHTML: '',
      addEventListener(name, fn) { this.listeners[name] = fn; }, focus() {}, replaceChildren() {}, append() {},
      querySelector: selector => node(id + selector), showModal() {}, close() {}, setAttribute() {},
      scrollTop: 0, scrollHeight: 0 });
    return nodes.get(id);
  };
  const PR = { $: id => node(id.replace(/^#/, '')), t: (s, v) => s.replace(/\{(\w+)\}/g, (_, k) => v?.[k]), esc: s => String(s), el: (_, attrs) => attrs };
  const context = vm.createContext({ window: { PR }, URLSearchParams, URL, setTimeout: fn => timers.push(fn),
    FileReader: class { readAsDataURL(file) { this.result = file.data; reads.push(() => this.onload()); } },
    document: { createTextNode: text => text, createElement: () => ({ append() {} }) } });
  const load = name => vm.runInContext(source(name), context);
  load('reader/chat-images.js');
  const paste = (panel, input, files, options = {}) => {
    let prevented = false;
    const items = options.filesOnly ? [] : files.map(file => ({ kind: 'file', type: options.emptyItemType ? '' : file.type, getAsFile: () => file }));
    const promise = node(panel).listeners.paste({ target: node(input), clipboardData: { items, files,
      getData: () => options.text || '' }, preventDefault() { prevented = true; } });
    return { promise, get prevented() { return prevented; } };
  };
  const finish = async () => { while (reads.length) { reads.shift()(); await settle(); } };
  return { PR, node, load, paste, finish, timers };
}

test('createChatImages: files fallback, empty MIME inference, mixed/plain text, validation, generation fence', async () => {
  const h = harness(), st = {}, draft = h.PR.createChatImages(st, () => h.node('panel'), () => {}, () => {}, { inputId: 'input', canAdd: () => true });
  assert.equal(h.paste('panel', 'input', [], { text: 'normal text' }).prevented, false);
  let event = h.paste('panel', 'input', [{ ...image, type: '' }], { filesOnly: true });
  assert.equal(event.prevented, true); assert.equal(st.imageLoading, true);
  await h.finish(); await event.promise;
  assert.deepEqual(plain(st.images), [{ data: image.data }]);
  draft.reset();
  event = h.paste('panel', 'input', [image], { emptyItemType: true }); await h.finish(); await event.promise;
  assert.equal(st.images.length, 1, 'items/files duplication does not double count');
  event = h.paste('panel', 'input', [image], { text: 'mixed caption' });
  assert.equal(event.prevented, false, 'native text insertion must remain enabled');
  assert.equal(st.images.length, 1); h.timers.shift()(); await h.finish(); assert.equal(st.images.length, 2);
  for (const [file, pattern] of [[{ ...image, type: 'image/gif' }, /仅支持/], [{ ...image, size: 8 * 1024 * 1024 + 1 }, /8 MB/]]) {
    await h.paste('panel', 'input', [file]).promise; assert.match(st.imageError, pattern); assert.equal(st.images.length, 2);
  }
  await draft.add([image, image, image]); assert.match(st.imageError, /4 张/);
  event = h.paste('panel', 'input', [image]); draft.reset(); await h.finish(); await event.promise;
  assert.equal(st.images.length, 0); assert.equal(st.imageLoading, false);
});

test('library-search: pasted image goes in API request, image-only default, read gate and failure retention', async () => {
  const h = harness(), calls = []; let fail = true;
  h.PR.api = async (url, options) => {
    calls.push({ url, body: plain(options.body) });
    if (fail) throw Error('offline');
    return { answer: 'ok', sources: [], usage: {}, sent: 0, model: 'fake' };
  };
  h.load('library/library-search.js');
  const submit = () => h.node('libraryAskform').listeners.submit({ preventDefault() {} });
  const event = h.paste('libraryAsk', 'libraryQuestion', [image], { filesOnly: true });
  await submit(); assert.equal(calls.length, 0);
  await h.finish(); await event.promise;
  assert.match(h.node('libraryImages').innerHTML, /data-c="unimage"/);
  await submit(); assert.deepEqual(calls[0], { url: '/api/library/ask', body: { query: '请解释这张图片。', images: [image.data] } });
  assert.match(h.node('libraryImages').innerHTML, /data-c="unimage"/);
  fail = false; await submit(); assert.equal(h.node('libraryImages').innerHTML, '');
  h.node('libraryQuestion').value = 'normal text'; await submit();
  assert.deepEqual(calls.at(-1).body, { query: 'normal text' });
});

function workspace() {
  const h = harness(), saved = new Map(), calls = [];
  const snapshot = id => ({ session: { id, engine: 'codex', state: 'idle', messages: [], events: [] }, cursor: 0 });
  const W = h.PR.workspaces = {
    $: h.node, key: key => 'project-a:' + key, project: { id: 'project-a', engine: 'codex' },
    store: (key, value) => { saved.set(W.key(key), plain(value)); return true; },
    sessionBusy: () => false, run: fn => fn, sessionLabel: () => '', goalStateLabel: () => '',
    sessionTerminal: { owns: () => false, observe() {}, render() {}, reconcile() {}, init() {} },
    mentions: { files: [], restore(draft) { this.files = draft?.files || []; }, close() {}, init() {} },
    renderSessionRequests() {}, renderConversation() {}, raw: name => '/raw?' + name,
    api: async (url, body) => {
      calls.push({ url, body: body && plain(body) });
      if (url === '/sessions') return { sessions: ['a', 'b'].map(id => snapshot(id).session), capabilities: {} };
      return snapshot(url.split('/')[2]);
    }
  };
  h.PR.ls = { get: (key, fallback) => saved.get(key) ?? fallback, del: key => saved.delete(key) };
  h.load('workspaces/session-images.js'); h.load('workspaces/session-history.js'); h.load('workspaces/sessions.js');
  return { ...h, W, calls, snapshot };
}

test('workspace sessions: actual image payload, fail/retry, remove, switch/new and async paste isolation', async () => {
  const h = workspace(), S = h.W.sessions;
  await S.init();
  let event = h.paste('ws-chat-form', 'ws-message', [image], { filesOnly: true });
  await S.send(); assert.ok(!h.calls.some(call => call.url.endsWith('/send')), 'pending reads gate send');
  await h.finish(); await event.promise;
  assert.match(h.node('ws-images').innerHTML, /data-c="unimage"/);
  await S.select('b'); assert.equal(h.node('ws-images').innerHTML, '');
  await S.select('a'); assert.match(h.node('ws-images').innerHTML, /data-c="unimage"/);
  const real = h.W.api;
  h.W.api = async (url, body) => { if (url.endsWith('/send')) throw Error('offline'); return real(url, body); };
  await assert.rejects(S.send(), /offline/); assert.match(h.node('ws-images').innerHTML, /data-c="unimage"/);
  h.node('ws-session-mode').value = 'goal'; S.capabilities = { engines: { codex: { goal: true } } };
  await assert.rejects(S.send(), /普通执行/); assert.equal(h.W.sessionImages.any, true);
  h.node('ws-session-mode').value = 'chat'; h.W.api = real;
  await S.send();
  assert.deepEqual(h.calls.find(call => call.url.endsWith('/send')).body,
    { message: '请解释这张图片。', files: [], images: [image.data], mode: 'chat' });
  assert.equal(h.W.sessionImages.any, false);
  event = h.paste('ws-chat-form', 'ws-message', [image]); await S.select('b'); await h.finish(); await event.promise;
  assert.equal(h.W.sessionImages.any, false, 'late reads do not leak to another conversation');
  await S.select('a'); assert.equal(h.W.sessionImages.any, false);
  event = h.paste('ws-chat-form', 'ws-message', [image]); await h.finish(); await event.promise;
  h.node('ws-images').listeners.click({ target: { closest: () => ({ dataset: { i: '0' } }) } });
  assert.equal(h.W.sessionImages.any, false);
  await S.select(''); event = h.paste('ws-chat-form', 'ws-message', [image]); await h.finish(); await event.promise;
  h.W.sessionImages.copyTo('created'); await S.select('created'); h.W.sessionImages.forget(null);
  assert.equal(h.W.sessionImages.any, true, 'initial composer images transfer to explicitly created conversation');
  await S.select('another-new'); assert.equal(h.W.sessionImages.any, false);
});

test('workspace sessions: a send completing after switch clears only its own image draft', async () => {
  const h = workspace(), S = h.W.sessions; await S.init();
  let event = h.paste('ws-chat-form', 'ws-message', [image]); await h.finish(); await event.promise;
  const real = h.W.api; let resolve;
  h.W.api = (url, body) => url.endsWith('/send') ? new Promise(done => { resolve = done; }) : real(url, body);
  const sending = S.send(); await settle(); await S.select('b');
  event = h.paste('ws-chat-form', 'ws-message', [image]); await h.finish(); await event.promise;
  resolve(h.snapshot('a')); await sending;
  assert.equal(h.W.sessionImages.any, true); await S.select('a'); assert.equal(h.W.sessionImages.any, false);
});

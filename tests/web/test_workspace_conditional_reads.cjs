const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const scripts = path.resolve(__dirname, '../../easyread/web/js/workspaces');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const turn = () => new Promise(resolve => setImmediate(resolve));
const file = (content = 'original', version = 'sha-1', stamp = 'stat-1', name = 'note.txt') => ({ path: name, content, version, stamp, binary: false });
const entry = (path, kind = 'file') => ({ path, kind, name: path.split('/').at(-1), version: 'metadata', size: 1 });
const tree = (entries = [], version = 'tree-1') => ({ entries, version });
const state = (version = 'latex-1', compilation = { state: 'idle', log: '', errors: [] }) => ({
  version, main: 'main.tex', engine: 'pdflatex', engines: [{ id: 'pdflatex', installed: true }], compilation,
});

function fixture() {
  const nodes = new Map(), stored = new Map(), requests = [];
  function el(tag = 'div', attrs = {}) {
    return Object.assign({ tag, children: [], value: '', hidden: false, disabled: false, dataset: {}, style: {},
      classList: { add() {}, remove() {}, toggle() {} }, selectedOptions: [{ disabled: false }],
      append(...items) { this.children.push(...items); }, prepend(...items) { this.children.unshift(...items); },
      replaceChildren(...items) { this.children = items; }, setAttribute(name, value) { this[name] = value; },
      focus() {}, querySelectorAll() { return []; },
    }, attrs);
  }
  const W = { view: 'project', project: { id: 'one' }, libraryId: 'library',
    key: key => W.libraryId + ':' + W.project.id + ':' + key,
    $: id => { if (!nodes.has(id)) nodes.set(id, el()); return nodes.get(id); },
    run: fn => fn, store: (key, value) => { stored.set(W.key(key), value); return true; },
    button: (text, onclick) => el('button', { text, onclick }), empty: text => el('div', { text }),
    editorSettings: { close() {} }, raw: path => '/raw/' + path, safePath: path => path,
    api: async (route, body) => { const url = new URL(route, 'http://test'); requests.push({ url, body }); return W.respond(url, body); },
  };
  const PR = { workspaces: W, el, t: s => s, ls: { get: (key, fallback) => stored.get(key) ?? fallback }, confirm: async () => true };
  const context = vm.createContext({ window: { PR, addEventListener() {} },
    document: { activeElement: null, addEventListener() {}, createTextNode: text => ({ text }), querySelector: () => el() },
    URL, URLSearchParams, setTimeout: () => 1, clearTimeout() {},
  });
  for (const name of ['buffers.js', 'files.js', 'latex.js']) vm.runInContext(fs.readFileSync(path.join(scripts, name), 'utf8'), context);
  const F = W.files, L = W.latex;
  const counts = { tree: 0, editor: 0, preview: 0, replace: 0, persist: 0, changed: 0 };
  F.renderTree = () => ++counts.tree; F.renderEditor = () => ++counts.editor;
  F.renderPreview = () => ++counts.preview; F.persist = () => { ++counts.persist; return true; };
  F.editor = { replace: () => ++counts.replace, destroy() {}, setReadOnly() {}, diagnostics() {} };
  L.sourceChanged = () => ++counts.changed;
  return { W, PR, F, L, requests, counts, stored };
}

function activate(PR, F, data = file(), draft) {
  const buffer = new PR.WorkspaceBuffer(data, draft); F.buffers.set(buffer.path, buffer); F.active = buffer; return buffer;
}

test('files.refresh: cached tree/file hints skip replacement and preserve dirty text, selection and binary preview', async () => {
  const { W, PR, F, counts, requests } = fixture();
  let remote = file();
  W.respond = url => url.pathname === '/tree' ? (url.searchParams.has('if_version') ? { unchanged: true, version: 'tree-1' } : tree([entry('note.txt')])) :
    (url.searchParams.get('if_stamp') === remote.stamp ? { unchanged: true, stamp: remote.stamp, path: remote.path } : remote);
  const buffer = activate(PR, F);
  await F.refresh();
  buffer.content = 'unsaved draft'; buffer.selection = { anchor: 2, head: 7 };
  const entries = JSON.stringify(F.entries);
  await F.refresh();
  assert.equal(requests.at(-2).url.searchParams.get('if_version'), 'tree-1');
  assert.equal(requests.at(-1).url.searchParams.get('if_stamp'), 'stat-1');
  assert.equal(counts.tree, 1); assert.equal(counts.replace, 0); assert.equal(buffer.content, 'unsaved draft');
  assert.equal(buffer.selection.head, 7); assert.equal(JSON.stringify(F.entries), entries);
  remote = file('external edit', 'sha-2', 'stat-2');
  await F.refresh();
  assert.equal(buffer.conflict, true); assert.equal(buffer.content, 'unsaved draft'); assert.equal(buffer.remote.content, 'external edit');
  await F.refresh(); assert.equal(requests.at(-1).url.searchParams.get('if_stamp'), 'stat-2');
  assert.equal(buffer.remote.content, 'external edit'); assert.equal(buffer.version, 'sha-1');
  remote = { path: 'image.png', version: 'png-sha', stamp: 'png-stat', mime: 'image/png', binary: true };
  activate(PR, F, remote); F.preview = true;
  await F.refresh(); await F.refresh();
  assert.equal(requests.at(-1).url.searchParams.get('if_stamp'), 'png-stat');
  assert.equal(counts.preview, 0); assert.equal(counts.editor, 0);
});

test('files.open/refresh: open seeds the hint, clean external changes update, full-only servers and unstable snapshots remain compatible', async () => {
  const { W, F, requests, counts } = fixture();
  let remote = file();
  W.respond = url => url.pathname === '/tree' ? tree() : remote;
  await F.open('note.txt'); await F.refresh();
  assert.equal(requests.at(-1).url.searchParams.get('if_stamp'), 'stat-1');
  remote = file('new full response', 'sha-2', undefined); delete remote.stamp;
  await F.refresh();
  assert.equal(F.active.content, 'new full response'); assert.equal(counts.replace, 1);
  await F.refresh(); assert.equal(requests.at(-1).url.searchParams.has('if_stamp'), false);
});

test('files.refresh: root/subtree caches replace deleted children, do not mix scopes, and prune deleted expansions', async () => {
  const { W, F, requests } = fixture();
  F.expanded.add('.git'); F.expanded.add('.git/objects');
  let generation = 1;
  W.respond = url => {
    const p = url.searchParams.get('path'), hint = url.searchParams.get('if_version');
    if (p === 'latex-workspace') return tree([entry('latex-workspace/main.tex')], 'latex-tree');
    if (p === '') return hint ? { unchanged: true, version: 'root' } : tree([entry('.git', 'directory'), entry('.git/objects', 'directory')], 'root');
    if (generation === 3) throw Object.assign(new Error('deleted'), { status: 404 });
    const version = p + generation;
    return hint === version ? { unchanged: true, version } : tree(generation === 1 ? [entry('.git/objects/old')] : [], version);
  };
  await F.refresh(); await F.refresh();
  assert.equal(F.entries.some(e => e.path === '.git/objects/old'), true);
  generation = 2; await F.refresh();
  assert.equal(F.entries.some(e => e.path === '.git/objects/old'), false);
  W.view = 'latex'; await F.refresh();
  assert.equal(requests.at(-1).url.searchParams.has('if_version'), false);
  assert.deepEqual(Array.from(F.entries, e => e.path), ['latex-workspace/main.tex']);
  W.view = 'project'; await F.refresh();
  assert.equal(F.entries.some(e => e.path.startsWith('latex-workspace')), false);
  generation = 3; await F.refresh();
  assert.equal(F.entries.length, 0); assert.equal(F.expanded.size, 0);
  W.project = { id: 'two' }; await F.refresh();
  assert.equal(requests.at(-1).url.searchParams.has('if_version'), false, 'another project never uses the previous root token');
});

test('files.refresh: stale overlapping tree responses, changed expansions and navigation cannot poison caches', async () => {
  const { W, F, requests } = fixture();
  const old = deferred(); let first = true;
  W.respond = () => first ? (first = false, old.promise) : tree([entry('latest')], 'latest');
  const pending = F.refresh(); await F.refresh(); old.resolve(tree([entry('old')], 'old')); await pending;
  assert.deepEqual(Array.from(F.entries, e => e.path), ['latest']);
  await F.refresh(); assert.equal(requests.at(-1).url.searchParams.get('if_version'), 'latest');
  const changing = deferred(); W.respond = () => changing.promise;
  const expanded = F.refresh(); F.expanded.add('folder'); changing.resolve(tree([entry('stale-expanded')], 'stale-expanded')); await expanded;
  W.respond = () => tree([entry('latest')], 'latest'); await F.refresh();
  assert.equal(requests.at(-1).url.searchParams.get('if_version'), 'latest');
  const switched = deferred(); W.respond = () => switched.promise;
  const navigating = F.refresh(); W.view = 'latex'; switched.resolve(tree([entry('project')], 'project')); await navigating;
  assert.deepEqual(Array.from(F.entries, e => e.path), ['latest']);
});

test('files.refresh: in-flight save/rename/close and obsolete 404s cannot overwrite or delete the current buffer', async () => {
  const { W, PR, F, requests } = fixture();
  const buffer = activate(PR, F); let response = file();
  W.respond = url => url.pathname === '/tree' ? tree() : response;
  await F.refresh();
  const pendingRead = deferred(), save = deferred();
  W.respond = (url, body) => url.pathname === '/tree' ? tree() : body ? save.promise : pendingRead.promise;
  const poll = F.refresh(); await turn();
  buffer.content = 'saved text'; const saving = F.save();
  save.resolve({ version: 'sha-saved' }); await saving;
  pendingRead.resolve(file('stale remote', 'sha-old', 'stat-old')); await poll;
  assert.equal(buffer.content, 'saved text'); assert.equal(buffer.version, 'sha-saved'); assert.equal(buffer.conflict, false);
  W.respond = url => url.pathname === '/tree' ? tree() : file('saved text', 'sha-saved', 'stat-saved');
  await F.refresh(); assert.equal(requests.at(-1).url.searchParams.has('if_stamp'), false);
  const missing = deferred(); W.respond = url => url.pathname === '/tree' ? tree() : missing.promise;
  const beforeRename = F.refresh(); await turn();
  F.buffers.delete(buffer.path); buffer.path = 'renamed.txt'; F.buffers.set(buffer.path, buffer);
  missing.reject(Object.assign(new Error('gone'), { status: 404 })); await beforeRename;
  assert.equal(buffer.deleted, false); assert.equal(buffer.conflict, false);
  const closing = deferred(); W.respond = url => url.pathname === '/tree' ? tree() : closing.promise;
  const beforeClose = F.refresh(); await turn(); await F.close();
  closing.resolve(file('obsolete', 'sha-old')); await beforeClose;
  assert.equal(F.active, null); assert.equal(buffer.content, 'saved text');
});

test('files.open: an autosave that overlaps opening cannot seed a stamp for unobserved content', async () => {
  const { W, PR, F, requests } = fixture();
  const buffer = activate(PR, F); buffer.content = 'draft to save';
  const read = deferred(), write = deferred();
  W.respond = (url, body) => body ? write.promise : read.promise;
  const opening = F.open(buffer.path), saving = F.save();
  read.resolve(file('obsolete', 'sha-old', 'stat-old')); await opening;
  assert.equal(buffer.content, 'draft to save'); assert.equal(buffer.saving, true);
  write.resolve({ version: 'sha-saved' }); await saving;
  W.respond = url => url.pathname === '/tree' ? tree() : file('draft to save', 'sha-saved', 'stat-saved');
  await F.refresh();
  assert.equal(requests.at(-1).url.searchParams.has('if_stamp'), false);
  assert.equal(buffer.content, 'draft to save'); assert.equal(buffer.conflict, false);
});

test('files.refresh: external deletion clears the stat hint and preserves the local draft', async () => {
  const { W, PR, F, requests } = fixture();
  const buffer = activate(PR, F); let missing = false;
  W.respond = url => {
    if (url.pathname === '/tree') return tree();
    if (missing) throw Object.assign(new Error('gone'), { status: 404 });
    return file();
  };
  await F.refresh(); buffer.content = 'keep my draft'; missing = true; await F.refresh();
  assert.equal(buffer.deleted, true); assert.equal(buffer.conflict, true); assert.equal(buffer.content, 'keep my draft');
  await F.refresh(); assert.equal(requests.at(-1).url.searchParams.has('if_stamp'), false);
});

test('latex.poll: unchanged retains full state/logs and rendering, complete updates retain errors and all state transitions', async () => {
  const { W, L, requests } = fixture(); W.view = 'latex';
  let remote = state('error-1', { state: 'error', log: 'full persisted log\n'.repeat(5000), errors: [{ file: 'main.tex', line: 7, message: 'error' }] });
  W.respond = url => url.searchParams.get('if_version') === remote.version ? { unchanged: true, version: remote.version } : remote;
  await L.enter(); const original = L.data;
  assert.equal(W.$('ws-latex-log-text').textContent, remote.compilation.log);
  await L.poll(); assert.equal(L.data, original); assert.equal(requests.at(-1).url.searchParams.get('if_version'), 'error-1');
  assert.equal(W.$('ws-latex-errors').children.length, 1); assert.equal(W.$('ws-latex-log').hidden, false);
  for (const phase of ['running', 'done', 'idle', 'error']) {
    remote = state(phase, { state: phase, log: 'complete ' + phase, errors: [] });
    await L.poll(); assert.equal(L.data.compilation.state, phase);
    assert.equal(W.$('ws-latex-log-text').textContent, 'complete ' + phase);
    assert.equal(W.$('ws-compile-cancel').hidden, phase !== 'running');
    assert.equal(W.$('ws-compile').hidden, phase === 'running');
    await L.poll(); assert.equal(L.data.compilation.log, 'complete ' + phase);
  }
  // A full-only/old server need not provide a version or honor the query.
  remote = state(); delete remote.version; await L.poll(); await L.poll();
  assert.equal(requests.at(-1).url.searchParams.has('if_version'), false);
});

test('latex.poll: settings writes, overlapping responses and leaving discard obsolete state without losing logs', async () => {
  const { W, L, requests } = fixture(); W.view = 'latex';
  W.respond = () => state(); await L.enter();
  const old = deferred(); let first = true;
  W.respond = () => first ? (first = false, old.promise) : state('newer');
  const pending = L.poll(); await L.poll(); old.resolve(state('older')); await pending;
  assert.equal(L.data.version, 'newer');
  const pollReply = deferred(), saveReply = deferred();
  W.respond = (url, body) => body ? saveReply.promise : pollReply.promise;
  const beforeSave = L.poll();
  W.$('ws-latex-main').value = 'changed.tex'; W.$('ws-latex-engine').value = 'pdflatex';
  const save = L.saveSettings(); pollReply.resolve(state('obsolete')); await beforeSave;
  assert.equal(L.data.version, 'newer');
  saveReply.resolve({}); await save; assert.equal(L.data.main, 'changed.tex');
  W.respond = () => ({ ...state('saved'), main: 'changed.tex' }); await L.poll();
  assert.equal(requests.at(-1).url.searchParams.has('if_version'), false);
  const leaving = deferred(); W.respond = () => leaving.promise;
  const beforeLeave = L.poll(); await L.leave(); leaving.resolve(state('late')); await beforeLeave;
  assert.equal(L.data.version, 'saved');
});

test('latex.importZip: pre-import polls cannot restore old state or cached source buffers', async () => {
  const { W, PR, F, L, requests } = fixture(); W.view = 'latex';
  W.sessions = { items: [] }; F.flush = async () => true; PR.toast = () => {};
  W.respond = () => state(); await L.enter();
  const path = 'latex-workspace/main.tex'; const original = activate(PR, F, file('source', 'sha-1', 'stat-1', path));
  const oldPoll = deferred(); W.respond = () => oldPoll.promise;
  const polling = L.poll();
  const imported = { ...state('imported'), imported: [path] };
  W.respond = url => url.pathname === '/latex/import-zip' ? imported :
    url.pathname === '/tree' ? tree([entry(path)]) : file('imported source', 'sha-new', 'stat-new', path);
  await L.importZip({ name: 'project.zip', size: 10 });
  oldPoll.resolve(state('obsolete')); await polling;
  assert.equal(L.data.version, 'imported'); assert.notEqual(F.active, original); assert.equal(F.active.content, 'imported source');
  W.respond = () => state('after-import'); await L.poll();
  assert.equal(requests.at(-1).url.searchParams.has('if_version'), false);
});

test('latex.poll: compile completion and unchanged renders re-enable controls without an extra full log', async () => {
  const { W, L } = fixture(); W.view = 'latex';
  W.files.flush = async () => true;
  let remote = state();
  W.respond = (url, body) => {
    if (url.pathname === '/latex/compile') { remote = state('compiling', { state: 'running', log: 'started', errors: [] }); return {}; }
    return url.searchParams.get('if_version') === remote.version ? { unchanged: true, version: remote.version } : remote;
  };
  await L.enter(); await L.compile();
  assert.equal(L.data.compilation.state, 'running'); assert.equal(W.$('ws-compile').disabled, true);
  remote = state('failed', { state: 'error', log: 'full failure log', errors: [{ message: 'failed' }] });
  await L.poll(); await L.poll();
  assert.equal(W.$('ws-compile').disabled, false); assert.equal(W.$('ws-latex-log-text').textContent, 'full failure log');
});

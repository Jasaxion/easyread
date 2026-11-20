const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
function load(name, PR = {}) {
  const context = { window: { PR }, URLSearchParams, fetch: (...args) => context.fetchImpl(...args), location: { pathname: '/workspaces', search: '?project=p', assign(url) { context.redirect = url; } } };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'easyread/web/js/workspaces', name), 'utf8'), context);
  return context;
}
const Buffer = load('buffers.js').window.PR.WorkspaceBuffer;
const file = (content = 'original', version = 'v1') => ({ path: 'main.tex', content, version, binary: false });

test('workspace buffer: clean remote changes refresh; dirty changes retain both versions', () => {
  const buffer = new Buffer(file());
  assert.equal(buffer.observe(file('agent edit', 'v2')), true);
  assert.equal(buffer.content, 'agent edit');
  buffer.content = 'browser draft';
  assert.equal(buffer.observe(file('next agent edit', 'v3')), false);
  assert.equal(buffer.content, 'browser draft');
  assert.equal(buffer.base, 'agent edit');
  assert.equal(buffer.version, 'v2');
  assert.equal(buffer.remote.content, 'next agent edit');
  assert.equal(buffer.conflict, true);
});
test('workspace buffer: 409 never retries or discards draft', async () => {
  const buffer = new Buffer(file()); buffer.content = 'draft'; let calls = 0;
  await assert.rejects(buffer.save(async body => {
    calls++; assert.equal(body.version, 'v1'); assert.equal(body.content, 'draft');
    const error = new Error('conflict'); error.status = 409; throw error;
  }), /conflict/);
  assert.equal(buffer.conflict, true); assert.equal(buffer.content, 'draft'); assert.equal(buffer.saving, false);
  assert.equal(await buffer.save(() => { calls++; }), false); assert.equal(calls, 1);
});
test('workspace buffer: edit during an in-flight save stays dirty against saved snapshot', async () => {
  const buffer = new Buffer(file()); buffer.content = 'submitted'; let resolve;
  const pending = buffer.save(() => new Promise(r => { resolve = r; }));
  buffer.content = 'new keystroke';
  assert.equal(buffer.observe(file('racing poll', 'v2')), false);
  assert.equal(await buffer.save(() => assert.fail('duplicate write')), false);
  resolve({ version: 'v2' }); await pending;
  assert.equal(buffer.base, 'submitted'); assert.equal(buffer.content, 'new keystroke'); assert.equal(buffer.dirty, true); assert.equal(buffer.version, 'v2');
});
test('workspace buffer: restored draft uses original expected version and detects server divergence', () => {
  const buffer = new Buffer(file('agent changed', 'v9'), { base: 'original', content: 'draft', version: 'v1', selection: { anchor: 2 } });
  assert.equal(buffer.content, 'draft'); assert.equal(buffer.version, 'v1'); assert.equal(buffer.conflict, true); assert.equal(buffer.selection.anchor, 2);
});
test('workspace buffer: unchanged restored snapshot may take server content; errors and deletion preserve draft', async () => {
  const buffer = new Buffer(file('remote', 'v2'), { base: 'original', content: 'original', version: 'v1' });
  assert.equal(buffer.content, 'remote'); buffer.content = 'draft';
  await assert.rejects(buffer.save(() => Promise.reject(new Error('offline'))), /offline/);
  assert.equal(buffer.dirty, true); assert.equal(buffer.conflict, false);
  buffer.deleted = true;
  assert.equal(await buffer.save(() => assert.fail('recreate without consent')), false);
});
test('workspace API: session credentials, token POST, raw upload and typed conflict', async () => {
  const context = load('core.js', { t: (s, vars) => s.replace(/\{(\w+)\}/g, (_, key) => vars[key]), token: 'token' });
  const W = context.window.PR.workspaces; W.project = { id: 'random-id' }; W.libraryId = 'library-a';
  let seen;
  context.fetchImpl = async (url, options) => { seen = { url, options }; return { ok: true, status: 200, json: async () => ({ ok: true }) }; };
  await W.api('/files/write', { path: 'main.tex', content: 'text', version: 'v1' });
  assert.equal(seen.url, '/api/workspaces/random-id/files/write'); assert.equal(seen.options.headers['X-Token'], 'token');
  assert.equal(seen.options.credentials, 'same-origin'); assert.equal(JSON.parse(seen.options.body).version, 'v1');
  const raw = new Uint8Array([1, 2]); await W.api('/files/upload?path=x', raw, true);
  assert.equal(seen.options.body, raw); assert.equal(seen.options.headers['Content-Type'], undefined);
  await W.api('/tree'); assert.equal(seen.options.method, 'GET'); assert.equal(seen.options.headers['X-Token'], undefined);
  context.fetchImpl = async () => ({ ok: false, status: 409, json: async () => ({ error: 'changed', code: 'conflict' }) });
  await assert.rejects(W.api('/files/write', {}), error => error.status === 409 && error.code === 'conflict');
  context.fetchImpl = async () => ({ ok: false, status: 401, json: async () => ({ login_required: true, error: 'login' }) });
  await assert.rejects(W.api('/tree')); assert.match(context.redirect, /^\/login\?next=/);
  assert.notEqual(W.key('session'), (W.libraryId = 'library-b', W.key('session')));
});
test('workspace paths reject traversal and preserve dotfiles while isolating LaTeX scope', () => {
  const W = load('core.js', { t: s => s }).window.PR.workspaces;
  for (const value of ['../x', '/tmp/x', 'x/../y', 'x//y', 'x\\y', 'x\0y']) assert.throws(() => W.safePath(value));
  assert.equal(W.safePath('.git/config'), '.git/config'); assert.equal(W.safePath('.beads/config.yaml'), '.beads/config.yaml');
  assert.equal(W.safePath('latex-workspace/main.tex', 'latex-workspace'), 'latex-workspace/main.tex');
  assert.throws(() => W.safePath('elsewhere/main.tex', 'latex-workspace'));
});

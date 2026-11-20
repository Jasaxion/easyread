const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { create } = require('../../easyread/web/js/reader/whiteboard-sync.js');
function setup(save, drafts) {
  const records = new Map(), states = []; let next = 0;
  const sync = create({ id: 'session1', scope: 'library:paper', revision: 'r1', scene: { elements: [] },
    uuid: () => 'mutation' + (++next), save, drafts: drafts || { put: async row => records.set(row.id, row), remove: async id => records.delete(id) },
    status: (state, detail) => states.push({ state, ...detail }) });
  return { sync, records, states };
}
const scene = text => ({ elements: [{ id: 'text', type: 'text', text }], files: { image: { dataURL: 'data:image/png;base64,AA==' } } });

test('does not allocate drafts or save before a change', async () => {
  let calls = 0; const { sync, records } = setup(async () => { calls++; });
  await sync.flush(); assert.equal(records.size, 0); assert.equal(calls, 0);
});
test('serializes saves and cannot acknowledge a newer edit', async () => {
  let release; const requests = [];
  const { sync, records } = setup(async snapshot => {
    requests.push(structuredClone(snapshot));
    if (requests.length === 1) await new Promise(resolve => { release = resolve; });
    return { revision: 'r' + (requests.length + 1) };
  });
  sync.change(scene('one')); const first = sync.flush();
  sync.change(scene('two')); await sync.flush();
  assert.equal(requests.length, 1); assert.equal(sync.dirty, true);
  release(); await first;
  assert.equal(requests.length, 2); assert.equal(requests[1].revision, 'r2');
  assert.equal(requests[0].scene.elements[0].text, 'one');
  assert.equal(requests[1].scene.elements[0].text, 'two');
  assert.equal(sync.dirty, false); assert.equal(records.size, 0);
});
test('lost response retries exact mutation before sending newer changes', async () => {
  const requests = []; const { sync } = setup(async snapshot => {
    requests.push(structuredClone(snapshot));
    if (requests.length === 1) throw Error('lost receipt');
    return { revision: requests.length === 2 ? 'r2' : 'r3' };
  });
  sync.change(scene('one')); await sync.flush();
  sync.change(scene('two')); await sync.flush();
  assert.deepEqual(requests[1], requests[0]);
  assert.equal(requests[2].revision, 'r2'); assert.equal(requests[2].scene.elements[0].text, 'two');
  assert.equal(sync.revision, 'r3'); assert.equal(sync.dirty, false);
});
test('conflict retains image draft and never force-overwrites', async () => {
  let calls = 0; const { sync, records, states } = setup(async () => { calls++; throw Object.assign(Error('conflict'), { status: 409 }); });
  sync.change(scene('local')); await sync.flush(); await sync.flush();
  assert.equal(calls, 1); assert.equal(sync.dirty, true);
  assert.equal(states.at(-1).state, 'conflict');
  assert.ok(records.get('session1').pending.scene.files.image);
  await sync.discard(); assert.equal(records.size, 0);
});
test('storage failure is visible even when network save succeeds', async () => {
  const { sync, states } = setup(async () => ({ revision: 'r2' }), { put: async () => { throw Error('quota'); }, remove: async () => { throw Error('quota'); } });
  sync.change(scene('important')); await sync.flush();
  assert.ok(states.some(row => row.state === 'storage-error'));
  assert.equal(states.at(-1).localError, true); assert.equal(sync.dirty, false);
});
test('restores interrupted in-flight save and removes source draft only after receipt', async () => {
  const requests = []; const { sync, records } = setup(async snapshot => { requests.push(snapshot); return { revision: 'r2' }; });
  const pending = { revision: 'r1', mutation: 'old-mutation', scene: scene('recover') };
  records.set('old', { id: 'old', scope: 'library:paper', pending, retry: pending });
  sync.restore(records.get('old')); assert.ok(records.has('old'));
  await sync.flush(); assert.deepEqual(requests[0], pending); assert.equal(records.size, 0);
});
test('editing a recovered stale draft retains its original revision until acknowledged', async () => {
  const requests = []; const { sync } = setup(async snapshot => { requests.push(snapshot); throw Object.assign(Error('conflict'), { status:409 }); });
  const pending = { revision:'older-revision', mutation:'recovered', scene:scene('old') };
  sync.restore({id:'old',pending}); sync.change(scene('edited after recovery')); await sync.flush();
  assert.equal(requests[0].revision,'older-revision'); assert.equal(sync.dirty,true); await sync.discard();
});

test('editor is locally isolated and not loaded by reader HTML', () => {
  const reader = fs.readFileSync('easyread/web/reader.html', 'utf8');
  const editor = fs.readFileSync('easyread/web/whiteboard.html', 'utf8');
  assert.doesNotMatch(reader, /vendor\/whiteboard/);
  assert.match(editor, /frame-src 'none'/);
  assert.ok(editor.indexOf('whiteboard-assets.js') < editor.indexOf('vendor/whiteboard/editor.js'));
  assert.match(fs.readFileSync('easyread/web/js/reader/reader-dock.js', 'utf8'), /moveBefore/);
});
